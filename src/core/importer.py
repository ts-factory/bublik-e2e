"""Import generated fixture bundles into Bublik through the API.

Authentication is a cookie session: POST ``/auth/login/`` with the admin
email/password, then reuse the cookie jar for every ``/api/v2/...`` call. The
target instance, credentials, and (optional) project/config setup are all driven
by CLI flags. UI import is intentionally not handled here — it lives in the
Playwright suite.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time as time_module
from typing import Any
import urllib.parse

from rich.live import Live
from rich.table import Table

from core.common import CliError, console, normalize_url, read_json, write_json
from core.manifest import generate_manifest
from core.settings import Settings, resolve_manifest
from core.summary import render_run_summary

# Manifest keys whose values embed the instance base URL (used when retargeting
# an import at a different host than the manifest was generated against).
_TOP_URL_KEYS = ("baseUrl", "uiBaseUrl", "dashboardUrl", "historyUrl", "importUrl")
_BUNDLE_URL_KEYS = ("importUrl", "runUrl", "logUrl", "runUrlTemplate", "logUrlTemplate")


def curl_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    cookie_jar: Path | None = None,
) -> Any:
    command = ["curl", "--silent", "--show-error", "--write-out", "\n%{http_code}"]
    if cookie_jar is not None:
        command.extend(["--cookie", str(cookie_jar), "--cookie-jar", str(cookie_jar)])
    if method != "GET":
        command.extend(["--request", method])
    if payload is not None:
        command.extend(
            [
                "--header",
                "Content-Type: application/json",
                "--data",
                json.dumps(payload),
            ]
        )
    command.append(url)
    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise CliError(f"curl failed for {url}: {completed.stderr.strip()}")
    body, separator, status_raw = completed.stdout.rpartition("\n")
    if not separator:
        raise CliError(f"curl did not return an HTTP status for {url}")
    try:
        status = int(status_raw)
    except ValueError as exc:
        raise CliError(f"invalid HTTP status returned for {url}: {status_raw!r}") from exc
    if status < 200 or status >= 300:
        raise CliError(f"HTTP {status} returned by {url}: {body.strip()}")
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise CliError(f"invalid JSON returned by {url}") from exc


def retarget_manifest_urls(manifest: dict[str, Any], old: str, new: str) -> None:
    """Rewrite the base URL in the manifest's known URL-bearing fields, in place.

    Only the documented URL keys are touched (not every string in the tree), so
    unrelated values that happen to contain the old host are never clobbered.
    """
    def swap(value: Any) -> Any:
        return value.replace(old, new) if isinstance(value, str) and old in value else value

    for key in _TOP_URL_KEYS:
        if key in manifest:
            manifest[key] = swap(manifest[key])
    for bundle in manifest.get("bundles", []):
        for key in _BUNDLE_URL_KEYS:
            if key in bundle:
                bundle[key] = swap(bundle[key])


def resolve_deep_links(manifest: dict[str, Any]) -> None:
    """Fill ``runUrl``/``logUrl`` from the per-bundle templates once runIds exist."""
    for bundle in manifest["bundles"]:
        run_id = bundle.get("runId")
        if not run_id:
            continue
        run_url = (bundle.get("runUrlTemplate") or "").replace("{runId}", str(run_id))
        log_url = (bundle.get("logUrlTemplate") or "").replace("{runId}", str(run_id))
        if run_url:
            bundle["runUrl"] = run_url
        if log_url:
            bundle["logUrl"] = log_url
        for expected in bundle.get("expectedRuns", []):
            if run_url:
                expected["runUrl"] = run_url
            if log_url:
                expected["logUrl"] = log_url


# Map an upper-cased import status to a Rich style for the STATUS cell.
_STATUS_STYLES = {
    "SUCCESS": "green",
    "DONE": "green",
    "FAILURE": "red",
    "RUNNING": "cyan",
    "PENDING": "dim",
}


class ProgressDisplay:
    """Render a live per-run import status table via Rich.

    Used as a context manager around the import polling loop; ``Live`` is
    started on enter and stopped on exit (success, timeout, or error). Reads run
    ids straight from the bundles; redraws are skipped when nothing changed.
    """

    def __init__(self, bundles: list[dict[str, Any]]) -> None:
        self.bundles = bundles
        self.last_snapshot: tuple[Any, ...] | None = None
        self.live = Live(console=console, refresh_per_second=4, transient=False)

    def __enter__(self) -> ProgressDisplay:
        self.live.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.live.stop()

    def update(self, status_by_id: dict[str, str]) -> None:
        rows = [
            (b["id"], status_by_id.get(b["id"], "PENDING"), b.get("runId"))
            for b in self.bundles
        ]
        snapshot = tuple(rows)
        if snapshot == self.last_snapshot:
            return
        self.last_snapshot = snapshot
        self.live.update(self._table(rows))

    def _table(self, rows: list[tuple[str, str, int | None]]) -> Table:
        done = sum(1 for _, _, run_id in rows if run_id)
        table = Table(title=f"Import progress: {done}/{len(rows)} imported")
        table.add_column("RUN", style="bold")
        table.add_column("STATUS")
        table.add_column("RUN ID", justify="right")
        for bid, status, run_id in rows:
            style = _STATUS_STYLES.get(status.upper(), "")
            table.add_row(
                bid,
                f"[{style}]{status}[/]" if style else status,
                str(run_id) if run_id else "-",
            )
        return table


def persist_imported_runs(
    manifest_path: Path,
    manifest: dict[str, Any],
    base_url: str,
    job_id: int,
    timeout: int,
) -> None:
    deadline = datetime.now().timestamp() + timeout
    bundles_by_url = {
        normalize_url(bundle["importUrl"]): bundle for bundle in manifest["bundles"]
    }
    status_by_id: dict[str, str] = {}
    last_payload: Any = None

    with ProgressDisplay(manifest["bundles"]) as display:
        while datetime.now().timestamp() < deadline:
            try:
                last_payload = curl_json(
                    f"{base_url}/api/v2/session_import/{job_id}/"
                )
            except CliError:
                last_payload = None
                time_module.sleep(1)
                continue

            for task in last_payload:
                bundle = bundles_by_url.get(normalize_url(task.get("run_source_url", "")))
                if bundle is None or bundle.get("runId"):
                    continue
                run_id = task.get("run_id")
                if isinstance(run_id, int) and run_id > 0:
                    bundle["runId"] = run_id
                status_by_id[bundle["id"]] = (
                    str(task.get("status", "") or "PENDING").upper()
                )

            display.update(status_by_id)

            if all(bundle.get("runId") for bundle in manifest["bundles"]):
                resolve_deep_links(manifest)
                write_json(manifest_path, manifest, True)
                return

            failed = [
                task
                for task in last_payload
                if str(task.get("status", "")).upper() == "FAILURE"
                and not (isinstance(task.get("run_id"), int) and task["run_id"] > 0)
            ]
            if failed:
                raise CliError(f"fixture import failed: {json.dumps(failed, indent=2)}")

            time_module.sleep(2)

    missing = [
        bundle["id"] for bundle in manifest["bundles"] if not bundle.get("runId")
    ]
    raise CliError(
        f"timed out waiting for import job {job_id}; missing bundles: {missing}; "
        f"last payload: {last_payload!r}"
    )


def login(base_url: str, settings: Settings, cookie_jar: Path) -> None:
    curl_json(
        f"{base_url}/auth/login/",
        method="POST",
        payload={"email": settings.email, "password": settings.password},
        cookie_jar=cookie_jar,
    )


def ensure_api_projects(
    manifest: dict[str, Any], base_url: str, cookie_jar: Path
) -> None:
    projects = curl_json(f"{base_url}/api/v2/projects/", cookie_jar=cookie_jar)
    projects_by_name = {project["name"]: project for project in projects}
    project_names = sorted({bundle["project"] for bundle in manifest["bundles"]})
    configs_by_project: dict[str, list[dict[str, Any]]] = {}
    for config in manifest.get("configs", []):
        configs_by_project.setdefault(config["project"], []).append(config)
    references = {
        "REVISIONS": {
            "TE_REV": {
                "uri": "https://github.com/ts-factory/test-environment",
                "name": "Test Environment",
            }
        },
        "LOGS_BASES": [
            {
                "uri": [f"{base_url}/logs/"],
                "name": "Local Logs Base",
            }
        ],
    }

    for project_name in project_names:
        project = projects_by_name.get(project_name)
        if project is None:
            project = curl_json(
                f"{base_url}/api/v2/projects/",
                method="POST",
                payload={"name": project_name},
                cookie_jar=cookie_jar,
            )
            projects_by_name[project_name] = project

        curl_json(
            f"{base_url}/api/v2/config/",
            method="POST",
            payload={
                "type": "global",
                "name": "references",
                "description": "E2E fixture logs references",
                "is_active": True,
                "content": references,
                "project": project["id"],
            },
            cookie_jar=cookie_jar,
        )

        for config in configs_by_project.get(project_name, []):
            curl_json(
                f"{base_url}/api/v2/config/",
                method="POST",
                payload={
                    "type": config["type"],
                    "name": config["name"],
                    "description": config.get("description", ""),
                    "is_active": True,
                    "content": config["content"],
                    "project": project["id"],
                },
                cookie_jar=cookie_jar,
            )


def import_via_api(
    args: argparse.Namespace, manifest: dict[str, Any], manifest_path: Path
) -> None:
    settings = Settings.from_args(args)
    # --url wins; otherwise fall back to the manifest's stored base.
    base_url = (settings.url_override or str(manifest.get("baseUrl", ""))).rstrip("/")
    if not base_url:
        raise CliError("no target URL: pass --url or use a manifest with baseUrl")

    old_base = str(manifest.get("baseUrl", "")).rstrip("/")
    if old_base and old_base != base_url:
        retarget_manifest_urls(manifest, old_base, base_url)

    render_run_summary(manifest, console, title="Importing runs")

    cookie_dir = Path(tempfile.mkdtemp(prefix="bublik-e2e-api-"))
    cookie_jar = cookie_dir / "cookies.txt"
    try:
        login(base_url, settings, cookie_jar)
        if getattr(args, "setup_projects", False):
            ensure_api_projects(manifest, base_url, cookie_jar)
        query = urllib.parse.urlencode({"url": manifest["importUrl"]})
        response = curl_json(
            f"{base_url}/api/v2/importruns/source/?{query}",
            cookie_jar=cookie_jar,
        )
    finally:
        shutil.rmtree(cookie_dir, ignore_errors=True)

    job_id = response.get("job_id")
    if not isinstance(job_id, int):
        raise CliError(f"import endpoint did not return a job_id: {response!r}")
    persist_imported_runs(manifest_path, manifest, base_url, job_id, args.timeout)
    console.print(
        f"[green]✓[/] Imported {len(manifest['bundles'])} fixture runs via API job "
        f"{job_id}"
    )


def import_manifest(args: argparse.Namespace) -> None:
    manifest_path = resolve_manifest(args)
    if not manifest_path.is_file():
        raise CliError(f"manifest not found: {manifest_path}")
    manifest = read_json(manifest_path)
    if manifest.get("version") != 1:
        raise CliError("only fixture manifest version 1 is supported")
    import_via_api(args, manifest, manifest_path)


def generate_and_import(args: argparse.Namespace) -> None:
    # The summary is rendered once, just before import (in import_via_api), so the
    # generate step suppresses its own copy to avoid showing the same table twice.
    generate_manifest(args, show_summary=False)
    import_manifest(args)
