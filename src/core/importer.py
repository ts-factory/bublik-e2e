"""Import generated fixture bundles into Bublik through the API.

Authentication is a cookie session: POST ``/auth/login/`` with the admin
email/password, then reuse the cookie jar for every ``/api/v2/...`` call. The
target instance, credentials, and (optional) project/config setup are all driven
by CLI flags.

The import is per-bundle: each bundle's ``importUrl`` is scheduled as its own
job, after reconciling the manifest's ``runId`` values against the instance's
import history (already-imported bundles are skipped, stale ids cleared). This
makes re-runs and imports into an already-populated instance idempotent.

Bundles marked ``importVia: ui`` are left for the Playwright suite, which
imports them through the UI import form (and thereby tests it); pass
``--include-ui`` to pull them through the API anyway.
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

from pydantic import ValidationError
from rich.live import Live

from core.common import CliError, console, normalize_url, read_json, write_json
from core.constants import NOK_BORDERS, RUN_COMPLETE_FILE
from core.manifest import generate_manifest
from core.manifest_models import Manifest
from core.settings import Settings, resolve_manifest
from core.summary import build_run_table, build_timing_summary, format_duration

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
    command = [
        "curl",
        "--silent",
        "--show-error",
        "--connect-timeout",
        "10",
        "--max-time",
        "30",
        "--write-out",
        "\n%{http_code}",
    ]
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
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=35,
        )
    except subprocess.TimeoutExpired as exc:
        raise CliError(f"curl timed out for {url}") from exc
    if completed.returncode != 0:
        raise CliError(f"curl failed for {url}: {completed.stderr.strip()}")
    body, separator, status_raw = completed.stdout.rpartition("\n")
    if not separator:
        raise CliError(f"curl did not return an HTTP status for {url}")
    try:
        status = int(status_raw)
    except ValueError as exc:
        raise CliError(
            f"invalid HTTP status returned for {url}: {status_raw!r}"
        ) from exc
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
        return (
            value.replace(old, new)
            if isinstance(value, str) and old in value
            else value
        )

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
        done = sum(
            1 for _, status, run_id in snapshot if status == "SUCCESS" and run_id
        )
        self.live.update(
            build_run_table(
                self.bundles,
                title=f"Importing runs: {done}/{len(self.bundles)} imported",
                status_by_id=status_by_id,
                base_url=self.base_url,
                show_import_columns=True,
            )
        )


def find_existing_run_id(
    base_url: str, import_url: str, cookie_jar: Path | None = None
) -> int | None:
    """Look up a previously imported run id for ``import_url``, if any.

    Queries the import-event log (``session_import``) filtered by source URL and
    returns the first successfully completed task that produced a run id. Used to
    reconcile the manifest against the database before scheduling: already-imported
    bundles are skipped, and stale or failed ``runId`` values are cleared.
    """
    query = urllib.parse.urlencode({"url": import_url, "page_size": 10000})
    try:
        payload = curl_json(
            f"{base_url}/api/v2/session_import/?{query}", cookie_jar=cookie_jar
        )
    except CliError:
        return None
    results = payload.get("results", []) if isinstance(payload, dict) else []
    expected = normalize_url(import_url)
    for task in results:
        if normalize_url(str(task.get("run_source_url", ""))) != expected:
            continue
        if str(task.get("status", "")).upper() != "SUCCESS":
            continue
        run_id = task.get("run_id")
        if isinstance(run_id, int) and run_id > 0:
            return run_id
    return None


def reconcile_run_ids(
    manifest: dict[str, Any], base_url: str, cookie_jar: Path | None = None
) -> None:
    """Refresh every bundle's ``runId`` from the database, in place.

    The database is the source of truth: a ``runId`` left in the manifest from a
    previous import is stale once the stack is brought up fresh, so values the
    database no longer has are cleared rather than trusted.
    """
    for bundle in manifest["bundles"]:
        bundle["runId"] = find_existing_run_id(
            base_url, bundle["importUrl"], cookie_jar
        )


def schedule_import(base_url: str, import_url: str, cookie_jar: Path) -> int:
    query = urllib.parse.urlencode({"url": import_url})
    response = curl_json(
        f"{base_url}/api/v2/importruns/source/?{query}", cookie_jar=cookie_jar
    )
    job_id = response.get("job_id") if isinstance(response, dict) else None
    if not isinstance(job_id, int):
        raise CliError(
            f"import endpoint did not return a job_id for {import_url}: {response!r}"
        )
    return job_id


def persist_imported_runs(
    manifest_path: Path,
    manifest: dict[str, Any],
    base_url: str,
    jobs: dict[str, int],
    timeout: int,
) -> dict[str, float]:
    # ``timeout`` is a no-progress budget, not a total cap: the deadline is reset
    # every time we observe the import advance (a new runId, or any per-task status
    # transition). Bublik imports runs sequentially, so a large batch keeps moving
    # for far longer than any fixed wall-clock limit would allow; we only give up
    # once the jobs have been completely silent for ``timeout`` seconds (a stuck or
    # dead worker).
    deadline = datetime.now().timestamp() + timeout
    # Only the bundles scheduled in ``jobs`` are waited for; bundles that already
    # had a runId (reconciled) or are reserved for UI import are not polled.
    target_bundles = [bundle for bundle in manifest["bundles"] if bundle["id"] in jobs]
    bundles_by_url = {
        normalize_url(bundle["importUrl"]): bundle for bundle in target_bundles
    }
    status_by_id: dict[str, str] = {}
    # Wall-clock time each bundle first received a run id, used to attribute the
    # sequential import's elapsed time to each fixture type in the final summary.
    completed_at: dict[str, float] = {}
    successful_ids: set[str] = set()
    last_payload: list[Any] = []
    prev_snapshot: tuple[Any, ...] | None = None

    with ProgressDisplay(target_bundles, base_url) as display:
        while datetime.now().timestamp() < deadline:
            # A run id can appear before the task reaches a terminal state. Keep
            # polling until SUCCESS so a later FAILURE cannot be missed.
            last_payload = []
            polled = False
            for bundle in target_bundles:
                if bundle["id"] in successful_ids:
                    continue
                try:
                    last_payload.extend(
                        curl_json(
                            f"{base_url}/api/v2/session_import/{jobs[bundle['id']]}/"
                        )
                    )
                    polled = True
                except CliError:
                    continue
            if not polled:
                time_module.sleep(1)
                continue

            for task in last_payload:
                bundle = bundles_by_url.get(
                    normalize_url(task.get("run_source_url", ""))
                )
                if bundle is None:
                    continue
                status = str(task.get("status", "") or "PENDING").upper()
                run_id = task.get("run_id")
                if isinstance(run_id, int) and run_id > 0:
                    bundle["runId"] = run_id
                status_by_id[bundle["id"]] = status
                if status == "SUCCESS" and bundle.get("runId"):
                    successful_ids.add(bundle["id"])
                    completed_at.setdefault(bundle["id"], datetime.now().timestamp())

            display.update(status_by_id)

            # Any change in completed runs or per-task status counts as progress and
            # extends the no-progress deadline.
            snapshot = (
                tuple(sorted(successful_ids)),
                tuple(
                    sorted(
                        (
                            task.get("run_source_url", ""),
                            str(task.get("status", "")).upper(),
                        )
                        for task in last_payload
                    )
                ),
            )
            if snapshot != prev_snapshot:
                prev_snapshot = snapshot
                deadline = datetime.now().timestamp() + timeout

            failed = [
                task
                for task in last_payload
                if str(task.get("status", "")).upper() == "FAILURE"
            ]
            if failed:
                raise CliError(f"fixture import failed: {json.dumps(failed, indent=2)}")

            if len(successful_ids) == len(target_bundles):
                resolve_deep_links(manifest)
                write_json(manifest_path, manifest, True)
                return completed_at

            time_module.sleep(2)

    missing = [
        bundle["id"] for bundle in target_bundles if bundle["id"] not in successful_ids
    ]
    # Persist only terminal successes. A nonterminal task may expose a run id before
    # later failing, so carrying that id into the manifest would incorrectly mark the
    # bundle as reusable.
    for bundle in target_bundles:
        if bundle["id"] not in successful_ids:
            bundle["runId"] = None
    saved_note = ""
    if successful_ids:
        resolve_deep_links(manifest)
        write_json(manifest_path, manifest, True)
        saved_note = "partial progress saved to manifest; "
    raise CliError(
        f"timed out after {timeout}s with no import progress; "
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

    include_ui = getattr(args, "include_ui", False)
    api_bundles: list[dict[str, Any]] = []
    ui_bundles: list[dict[str, Any]] = []
    for bundle in manifest["bundles"]:
        if not include_ui and bundle.get("importVia", "api") == "ui":
            ui_bundles.append(bundle)
        else:
            api_bundles.append(bundle)

    cookie_dir = Path(tempfile.mkdtemp(prefix="bublik-e2e-api-"))
    cookie_jar = cookie_dir / "cookies.txt"
    try:
        login(base_url, settings, cookie_jar)
        if getattr(args, "setup_projects", False):
            ensure_api_projects(manifest, base_url, cookie_jar)
        reconcile_run_ids(manifest, base_url, cookie_jar)
        import_start = datetime.now().timestamp()
        # One import job per bundle: this is what lets already-imported bundles
        # be skipped on re-runs against a live instance, and keeps bundles marked
        # importVia=ui out of the API import (they are the Playwright suite's).
        jobs = {
            bundle["id"]: schedule_import(base_url, bundle["importUrl"], cookie_jar)
            for bundle in api_bundles
            if not bundle.get("runId")
        }
    finally:
        shutil.rmtree(cookie_dir, ignore_errors=True)

    reused = sum(1 for bundle in api_bundles if bundle.get("runId"))
    if jobs:
        completed_at = persist_imported_runs(
            manifest_path, manifest, base_url, jobs, args.timeout
        )
    else:
        completed_at = {}
        resolve_deep_links(manifest)
        write_json(manifest_path, manifest, True)

    elapsed = datetime.now().timestamp() - import_start
    parts = [f"imported {len(jobs)} fixture runs via API in {format_duration(elapsed)}"]
    if reused:
        parts.append(f"reused {reused} already imported")
    if ui_bundles:
        pending_ui = sum(1 for bundle in ui_bundles if not bundle.get("runId"))
        parts.append(
            f"left {pending_ui} of {len(ui_bundles)} UI-import bundles "
            "for the Playwright suite (importVia=ui)"
        )
    console.print(f"[green]✓[/] {'; '.join(parts)}")
    timing = build_timing_summary(manifest["bundles"], completed_at, import_start)
    if timing is not None:
        console.print(timing)


def import_manifest(args: argparse.Namespace) -> None:
    manifest_path = resolve_manifest(args)
    if not manifest_path.is_file():
        raise CliError(f"manifest not found: {manifest_path}")
    manifest = read_json(manifest_path)
    try:
        Manifest.model_validate(manifest)
    except ValidationError as exc:
        raise CliError(f"manifest failed schema validation:\n{exc}") from exc
    import_via_api(args, manifest, manifest_path)


def generate_and_import(args: argparse.Namespace) -> None:
    # The summary is rendered once, just before import (in import_via_api), so the
    # generate step suppresses its own copy to avoid showing the same table twice.
    generate_manifest(args, show_summary=False)
    import_manifest(args)
