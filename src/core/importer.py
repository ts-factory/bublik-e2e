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

from core.common import CliError, console, normalize_url, read_json, write_json
from core.constants import NOK_BORDERS, RUN_COMPLETE_FILE
from core.manifest import generate_manifest
from core.settings import Settings, resolve_manifest
from core.summary import build_run_table

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


class ProgressDisplay:
    """Render a live per-run import status table via Rich.

    Used as a context manager around the import polling loop; ``Live`` is
    started on enter and stopped on exit (success, timeout, or error). Reads run
    ids straight from the bundles; redraws are skipped when nothing changed. The
    table is the same run summary used elsewhere, extended with live STATUS / RUN
    ID / LINK columns (see ``build_run_table``).
    """

    def __init__(self, bundles: list[dict[str, Any]], base_url: str) -> None:
        self.bundles = bundles
        self.base_url = base_url
        self.last_snapshot: tuple[Any, ...] | None = None
        self.live = Live(console=console, refresh_per_second=4, transient=False)

    def __enter__(self) -> ProgressDisplay:
        self.live.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.live.stop()

    def update(self, status_by_id: dict[str, str]) -> None:
        snapshot = tuple(
            (b["id"], status_by_id.get(b["id"], "PENDING"), b.get("runId"))
            for b in self.bundles
        )
        if snapshot == self.last_snapshot:
            return
        self.last_snapshot = snapshot
        done = sum(1 for *_, run_id in snapshot if run_id)
        self.live.update(
            build_run_table(
                self.bundles,
                title=f"Importing runs: {done}/{len(self.bundles)} imported",
                status_by_id=status_by_id,
                base_url=self.base_url,
                show_import_columns=True,
            )
        )


def persist_imported_runs(
    manifest_path: Path,
    manifest: dict[str, Any],
    base_url: str,
    job_id: int,
    timeout: int,
) -> None:
    # ``timeout`` is a no-progress budget, not a total cap: the deadline is reset
    # every time we observe the import advance (a new runId, or any per-task status
    # transition). Bublik imports runs sequentially, so a large batch keeps moving
    # for far longer than any fixed wall-clock limit would allow; we only give up
    # once the job has been completely silent for ``timeout`` seconds (a stuck or
    # dead worker).
    deadline = datetime.now().timestamp() + timeout
    bundles_by_url = {
        normalize_url(bundle["importUrl"]): bundle for bundle in manifest["bundles"]
    }
    status_by_id: dict[str, str] = {}
    last_payload: Any = None
    prev_snapshot: tuple[Any, ...] | None = None

    with ProgressDisplay(manifest["bundles"], base_url) as display:
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

            # Any change in completed runs or per-task status counts as progress and
            # extends the no-progress deadline.
            snapshot = (
                sum(1 for bundle in manifest["bundles"] if bundle.get("runId")),
                tuple(sorted(
                    (task.get("run_source_url", ""), str(task.get("status", "")).upper())
                    for task in (last_payload or [])
                )),
            )
            if snapshot != prev_snapshot:
                prev_snapshot = snapshot
                deadline = datetime.now().timestamp() + timeout

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
    # Persist whatever run ids landed before the stall so a re-run can resume and the
    # imported runs keep working deep-links.
    saved_note = ""
    if any(bundle.get("runId") for bundle in manifest["bundles"]):
        resolve_deep_links(manifest)
        write_json(manifest_path, manifest, True)
        saved_note = "partial progress saved to manifest; "
    raise CliError(
        f"timed out after {timeout}s with no import progress for job {job_id}; "
        f"{saved_note}missing bundles: {missing}; last payload: {last_payload!r}"
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
    meta = {
        "Lab": {
            "type": "label",
            "set-patterns": ["fixture"],
            "set-priority": 1,
        },
        "Mix": {
            "type": "tag",
            "set-comment": "Fixture under test",
            "set-patterns": ["^mix$"],
            "set-priority": 2,
        },
        "User": {
            "type": "label",
            "set-patterns": ["USER"],
        },
        "Device": {
            "type": "tag",
            "set-comment": "Fixture under test",
            "set-patterns": ["^device$"],
            "set-priority": 2,
        },
        "Status": {
            "type": "label",
            "set-patterns": ["RUN_STATUS"],
        },
        "Fixture": {
            "type": "tag",
            "set-comment": "Fixture under test",
            "set-patterns": ["^fixture$"],
            "set-priority": 2,
        },
        "Conclusion": {
            "type": "tag",
            "set-comment": "Test conclusion",
            "set-patterns": ["^conclusion$"],
            "set-priority": 2,
        },
        "Fixture Id": {
            "type": "tag",
            "set-comment": "Fixture under test",
            "set-patterns": ["^fixture_id$"],
            "set-priority": 2,
        },
        "Test Suite": {
            "type": "label",
            "set-patterns": ["TS_NAME"],
        },
        "Configuration": {
            "type": "label",
            "set-patterns": ["CFG"],
        },
    }
    per_conf = {
        "EMAIL_FROM": "noreply@ts-factory.io",
        "EMAIL_HOST": "localhost",
        "EMAIL_PORT": 25,
        "UI_VERSION": 2,
        "EMAIL_ADMINS": ["bublik@ts-factory.io"],
        "EMAIL_TIMEOUT": 60,
        "EMAIL_USE_TLS": True,
        "RUN_KEY_METAS": ["START_TIMESTAMP", "CFG"],
        "DASHBOARD_DATE": "CAMPAIGN_DATE",
        "RUN_STATUS_META": "RUN_STATUS",
        "TAB_TITLE_PREFIX": "Main",
        "DASHBOARD_COLUMNS": [
            {"key": "Test Suite", "payload": "go_report"},
            {"key": "Configuration", "payload": "go_run"},
            {"key": "Status"},
            {"key": "progress", "label": "Executed", "formatting": "percent"},
            {"key": "total", "label": "Total", "payload": "go_log"},
            {"key": "unexpected", "label": "NOK", "payload": "go_run_failed"},
            {"key": "Notes", "payload": "go_bug"},
        ],
        "METADATA_ON_PAGES": ["Configuration", "Test Suite"],
        "RUN_COMPLETE_FILE": RUN_COMPLETE_FILE,
        "SPECIAL_CATEGORIES": ["Configuration"],
        "DASHBOARD_RUNS_SORT": ["start"],
        "CSRF_TRUSTED_ORIGINS": [],
        "DASHBOARD_DEFAULT_MODE": "two_days_two_columns",
        "EMAIL_PROJECT_WATCHERS": [],
        "RUN_STATUS_BY_NOK_BORDERS": list(NOK_BORDERS),
        "FILES_TO_GENERATE_METADATA": ["meta_data.txt"],
        "NOT_PERMISSION_REQUIRED_ACTIONS": [],
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

        curl_json(
            f"{base_url}/api/v2/config/",
            method="POST",
            payload={
                "type": "global",
                "name": "meta",
                "description": "Meta categorization configuration",
                "is_active": True,
                "content": meta,
                "project": project["id"],
            },
            cookie_jar=cookie_jar,
        )

        curl_json(
            f"{base_url}/api/v2/config/",
            method="POST",
            payload={
                "type": "global",
                "name": "per_conf",
                "description": "Main project configuration",
                "is_active": True,
                "content": per_conf,
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

    # Bring the default (project=None) per_conf in line with the per-project
    # ones. It already exists from Bublik init, so a POST would be rejected by
    # the unique (type, name, project) check; PATCH the active version instead,
    # which creates a new version when the content differs and activates it.
    existing_configs = curl_json(f"{base_url}/api/v2/config/", cookie_jar=cookie_jar)
    default_per_conf = next(
        (
            config
            for config in existing_configs
            if config["type"] == "global"
            and config["name"] == "per_conf"
            and config["project"] is None
        ),
        None,
    )
    if default_per_conf is not None:
        curl_json(
            f"{base_url}/api/v2/config/{default_per_conf['id']}/",
            method="PATCH",
            payload={
                "description": "Main project configuration",
                "is_active": True,
                "content": per_conf,
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
