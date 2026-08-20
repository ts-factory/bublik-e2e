"""Build the e2e manifest: expectations, samples, and UI navigation metadata."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import tempfile
import re
from typing import Any
import urllib.parse
import uuid

from pydantic import ValidationError

from core.bundle import (
    apply_mix,
    generate_bundle,
    get_meta_value,
    leaf_tests,
    spec_from_plan,
)
from core.common import CliError, console, read_json, write_json
from core.constants import (
    ABNORMAL_STATUSES,
    EXPECTED_CONCLUSION,
    LOG_PAGES_MIN_ROWS,
    MATRIX_KEYS,
    NOK_BORDERS,
    RUN_STATUS_BY_CONCLUSION,
)
from core.discovery import selected_fixtures
from core.manifest_models import Manifest
from core.planning import build_mixes, build_plan
from core.run_log_schema import (
    load_meta_data_validator,
    load_run_log_validator,
    validate_meta_data,
    validate_run_log,
)
from core.settings import Settings, resolve_manifest
from core.summary import render_run_summary

REVISION_SUFFIXES = {"_GIT_URL": "url", "_BRANCH": "branch", "_REV": "rev"}


def campaign_date_from_start_timestamp(start_timestamp: str) -> str:
    timestamp = start_timestamp.replace("Z", "+00:00")
    return datetime.fromisoformat(timestamp).date().isoformat()


def flatten_iterations(
    bundle_dir: Path,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    iterations: list[dict[str, Any]] = []
    matrix: dict[str, list[dict[str, Any]]] = {key: [] for key in MATRIX_KEYS}

    for node in leaf_tests(bundle_dir):
        obtained = (
            node.get("obtained", {}).get("result", {}).get("status", "INCOMPLETE")
        )
        expected_values = [
            item.get("status")
            for item in node.get("expected", {}).get("results", [])
            if item.get("status")
        ]
        expected_status = expected_values[0] if expected_values else "PASSED"
        unexpected = obtained != expected_status
        entry = {
            "name": node.get("name"),
            "tin": node.get("tin"),
            "path": node.get("path", []),
            "pathStr": node.get("path_str", ""),
            "params": node.get("params", {}),
            "reqs": node.get("reqs", []),
            "status": obtained,
            "expectedStatus": expected_status,
            "unexpected": unexpected,
            "verdicts": node.get("obtained", {}).get("result", {}).get("verdicts", []),
            "artifacts": node.get("obtained", {})
            .get("result", {})
            .get("artifacts", []),
            "measurements": node.get("measurements", []),
        }
        iterations.append(entry)
        key = matrix_key(obtained, unexpected)
        if key:
            matrix[key].append(entry)
        if obtained in ABNORMAL_STATUSES:
            matrix["abnormal"].append(entry)

    return iterations, matrix


def matrix_key(status: str, unexpected: bool) -> str | None:
    prefix = "unexpected" if unexpected else "expected"
    suffix_by_status = {
        "PASSED": "Passed",
        "FAILED": "Failed",
        "SKIPPED": "Skipped",
        "KILLED": "Killed",
        "CORED": "Cored",
        "FAKED": "Faked",
        "INCOMPLETE": "Incomplete",
    }
    suffix = suffix_by_status.get(status)
    return f"{prefix}{suffix}" if suffix else None


def sample_tests_from_matrix(
    matrix: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    return {key: matrix.get(key, [])[:3] for key in MATRIX_KEYS}


def get_expected_run_name(bundle_dir: Path) -> str:
    meta = read_json(bundle_dir / "meta_data.json")
    for name in ("MAIN_PACKAGE", "NAME", "TS_NAME"):
        for item in meta.get("metas", []):
            if item.get("name") == name:
                return item["value"]
    return "example-ci"


def get_dashboard_date(bundle_dir: Path) -> str:
    meta = read_json(bundle_dir / "meta_data.json")
    campaign_date = get_meta_value(meta.get("metas", []), "CAMPAIGN_DATE")
    if campaign_date:
        return campaign_date
    start_timestamp = get_meta_value(meta.get("metas", []), "START_TIMESTAMP")
    if not start_timestamp:
        raise CliError(f"START_TIMESTAMP not found in {bundle_dir / 'meta_data.json'}")
    return campaign_date_from_start_timestamp(start_timestamp)


def expected_status_by_nok(matrix: dict[str, list[dict[str, Any]]]) -> str:
    total = sum(
        len(matrix[key])
        for key in MATRIX_KEYS
        if key != "abnormal" and key.startswith(("expected", "unexpected"))
    )
    unexpected = sum(
        len(matrix[key]) for key in MATRIX_KEYS if key.startswith("unexpected")
    )
    unexpected_percent = round(unexpected / total * 100) if total else 0
    if total == 0 or unexpected_percent >= NOK_BORDERS[1]:
        return "error"
    if NOK_BORDERS[0] < unexpected_percent < NOK_BORDERS[1]:
        return "warning"
    return "success"


def expected_reason(
    conclusion: str, matrix: dict[str, list[dict[str, Any]]]
) -> str | None:
    if conclusion == "compromised":
        return "Run marked as compromised"
    if conclusion in {"warning", "error", "running", "busy", "stopped", "interrupted"}:
        return f"RUN_STATUS reported by TE is {RUN_STATUS_BY_CONCLUSION[conclusion]}"
    if conclusion in {"nok-warning", "nok-error"}:
        total = sum(
            len(matrix[key])
            for key in MATRIX_KEYS
            if key != "abnormal" and key.startswith(("expected", "unexpected"))
        )
        unexpected = sum(
            len(matrix[key]) for key in MATRIX_KEYS if key.startswith("unexpected")
        )
        return (
            f"{round(unexpected / total * 100) if total else 0}% of the results "
            "are unexpected"
        )
    return None


def parse_revisions(metas: list[dict[str, Any]]) -> list[dict[str, str]]:
    grouped: dict[str, dict[str, str]] = {}
    for meta in metas:
        name = meta.get("name", "")
        for suffix, field in REVISION_SUFFIXES.items():
            if name.endswith(suffix) and len(name) > len(suffix):
                prefix = name[: -len(suffix)]
                grouped.setdefault(prefix, {"name": prefix})[field] = meta.get("value")
                break
    return [grouped[key] for key in sorted(grouped)]


def collect_packages(bundle_dir: Path) -> list[dict[str, Any]]:
    bublik = read_json(bundle_dir / "bublik.json")
    root = bublik["iters"][0]
    packages: list[dict[str, Any]] = []

    def status_counts(node: dict[str, Any]) -> dict[str, int]:
        counts: dict[str, int] = {}

        def visit(current: dict[str, Any]) -> None:
            children = current.get("iters") or []
            if current.get("type") == "test" and not children:
                status = (
                    current.get("obtained", {})
                    .get("result", {})
                    .get("status", "INCOMPLETE")
                )
                counts[status] = counts.get(status, 0) + 1
            for child in children:
                visit(child)

        visit(node)
        return counts

    child_packages = [
        child for child in (root.get("iters") or []) if child.get("type") == "pkg"
    ]
    for package in child_packages or [root]:
        counts = status_counts(package)
        packages.append(
            {
                "name": package.get("name"),
                "total": sum(counts.values()),
                "byStatus": dict(sorted(counts.items())),
            }
        )
    return packages


#: The page-one file of a node. Pages above one add ``_p<N>``; the whole log
#: combined is ``_all``. Both are found from this file's name, not matched here.
LOG_NODE_FILE_RE = re.compile(r"node_id(\d+)\.json")


def _table_row_count(payload: dict[str, Any]) -> int:
    block = (payload.get("root") or [{}])[0]
    return sum(
        len(item.get("data") or [])
        for item in block.get("content") or []
        if item.get("type") == "te-log-table"
    )


def collect_log_pages(bundle_dir: Path) -> list[dict[str, Any]]:
    """Describe the leaves whose logs a test could usefully page or scroll.

    Read back off the published files rather than predicted from the fixture:
    how a log is split is decided when it is written, and a provider that starts
    or stops paginating is picked up here without touching this code.
    """
    json_dir = bundle_dir / "json"
    if not json_dir.is_dir():
        return []

    bublik = read_json(bundle_dir / "bublik.json")
    roots = bublik.get("iters") or []
    if not roots:
        return []

    leaves: dict[int, dict[str, Any]] = {}

    def collect(node: dict[str, Any]) -> None:
        children = node.get("iters") or []
        if node.get("type") == "test" and not children:
            leaves[node["test_id"]] = node
        for child in children:
            collect(child)

    collect(roots[0])

    # One directory listing: a synthetic bundle has thousands of nodes and must
    # not cost a stat call each.
    names = {entry.name for entry in os.scandir(json_dir)}
    entries: list[dict[str, Any]] = []
    for name in sorted(names):
        match = LOG_NODE_FILE_RE.fullmatch(name)
        if not match:
            continue
        node = leaves.get(int(match.group(1)))
        if node is None:
            continue

        page_one = read_json(json_dir / name)
        block = (page_one.get("root") or [{}])[0]
        pages_count = int((block.get("pagination") or {}).get("pages_count") or 0) or 1
        if pages_count > 1:
            # The all-pages file reports zeros, so the row total comes from it
            # while the page count comes from page one.
            row_count = _table_row_count(
                read_json(json_dir / f"node_id{match.group(1)}_all.json")
            )
        else:
            row_count = _table_row_count(page_one)
            if row_count < LOG_PAGES_MIN_ROWS:
                continue

        entries.append(
            {
                "name": node.get("name"),
                "path": node.get("path", []),
                "pathStr": node.get("path_str", ""),
                "tin": node.get("tin"),
                "pagesCount": pages_count,
                "rowCount": row_count,
            }
        )

    return sorted(entries, key=lambda entry: (entry["pathStr"], entry["tin"] or 0))


def summarize_measurements(iterations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for leaf in iterations:
        for measurement in leaf.get("measurements") or []:
            tool = measurement.get("tool")
            metric = (measurement.get("keys") or {}).get("metric")
            for result in measurement.get("results") or []:
                for entry in result.get("entries") or []:
                    summaries.append(
                        {
                            "testPath": leaf.get("pathStr"),
                            "tool": tool,
                            "metric": metric or result.get("name"),
                            "value": entry.get("value"),
                            "units": entry.get("base_units"),
                        }
                    )
    return summaries


def unique_sorted(values: Any) -> list[str]:
    collected: set[str] = set()
    for value in values:
        if isinstance(value, str) and value:
            collected.add(value)
    return sorted(collected)


def _remove_publication(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _rollback_publication(publish_dir: Path, backup_dir: Path | None) -> None:
    """Restore the prior publication without risking loss of the replacement."""
    if backup_dir is None:
        _remove_publication(publish_dir)
        return

    failed_dir = publish_dir.parent / f".{publish_dir.name}.failed-{uuid.uuid4().hex}"
    os.replace(publish_dir, failed_dir)
    try:
        os.replace(backup_dir, publish_dir)
    except BaseException:
        os.replace(failed_dir, publish_dir)
        raise
    _remove_publication(failed_dir)


def generate_manifest(args: argparse.Namespace, *, show_summary: bool = True) -> None:
    settings = Settings.from_args(args)
    publish_dir = settings.publish_dir
    if publish_dir is None:
        raise CliError(
            "no publish directory: pass --publish-dir <path> "
            "(or set BUBLIK_E2E_PUBLISH_DIR)"
        )
    run_log_schema_path = settings.run_log_schema
    if run_log_schema_path is None:
        raise CliError(
            "no run-log schema: pass --run-log-schema <path> "
            "(or set BUBLIK_E2E_RUN_LOG_SCHEMA or BUBLIK_DJANGO_ROOT)"
        )
    meta_data_schema_path = settings.meta_data_schema
    if meta_data_schema_path is None:
        raise CliError(
            "no meta-data schema: pass --meta-data-schema <path> "
            "(or set BUBLIK_E2E_META_DATA_SCHEMA or BUBLIK_DJANGO_ROOT)"
        )
    run_log_validator = load_run_log_validator(run_log_schema_path)
    meta_data_validator = load_meta_data_validator(meta_data_schema_path)
    manifest_path = resolve_manifest(args)
    seg = publish_dir.name
    logs_base = settings.logs_base_url.rstrip("/")
    run_url_template = settings.run_url_template
    log_url_template = settings.log_url_template
    fixtures = selected_fixtures(args)
    mixes = build_mixes(args)
    planned_runs, empty_dates = build_plan(args, fixtures, mixes)
    bundles: list[dict[str, Any]] = []
    publication_dir = (
        publish_dir.resolve(strict=False) if publish_dir.is_symlink() else publish_dir
    )
    publication_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(
        tempfile.mkdtemp(
            prefix=f".{publication_dir.name}.staging-", dir=publication_dir.parent
        )
    )

    try:
        for plan in planned_runs:
            if plan.mix_name not in mixes:
                raise CliError(f"unknown mix {plan.mix_name!r}")
            spec = spec_from_plan(plan)
            bundle_output_dir = staging_dir / spec.id
            generate_bundle(plan.fixture, spec, bundle_output_dir, args.pretty)
            apply_mix(
                bundle_output_dir,
                mixes[plan.mix_name],
                spec.conclusion,
                args.pretty,
            )
            validate_run_log(
                bundle_output_dir / "bublik.json",
                run_log_schema_path,
                run_log_validator,
            )
            validate_meta_data(
                bundle_output_dir / "meta_data.json",
                meta_data_schema_path,
                meta_data_validator,
            )

            quoted = "/".join(
                urllib.parse.quote(part) for part in (seg, spec.id) if part
            )
            import_url = f"{logs_base}/{quoted}"

            meta = read_json(bundle_output_dir / "meta_data.json")
            bublik = read_json(bundle_output_dir / "bublik.json")
            metas = meta.get("metas", [])
            iterations, matrix = flatten_iterations(bundle_output_dir)
            status_by_nok = expected_status_by_nok(matrix)
            requirements = unique_sorted(
                req for leaf in iterations for req in leaf.get("reqs", [])
            )
            verdicts = unique_sorted(
                verdict for leaf in iterations for verdict in leaf.get("verdicts", [])
            )
            measurements = summarize_measurements(iterations)
            packages = collect_packages(bundle_output_dir)
            log_pages = collect_log_pages(bundle_output_dir)

            bundles.append(
                {
                    "id": spec.id,
                    "fixture": spec.fixture_name,
                    "conclusionSpec": spec.conclusion,
                    "mix": spec.mix_name,
                    "date": spec.run_date,
                    "importUrl": import_url,
                    "importVia": plan.import_via,
                    "project": spec.project,
                    "e2eRunId": spec.fixture_id,
                    "runStatus": get_meta_value(metas, "RUN_STATUS"),
                    "startTimestamp": get_meta_value(metas, "START_TIMESTAMP"),
                    "finishTimestamp": get_meta_value(metas, "FINISH_TIMESTAMP"),
                    "tags": bublik.get("tags", {}),
                    "revisions": parse_revisions(metas),
                    "runUrlTemplate": run_url_template,
                    "logUrlTemplate": log_url_template,
                    "expectedRuns": [
                        {
                            "name": get_expected_run_name(bundle_output_dir),
                            "dashboardDate": get_dashboard_date(bundle_output_dir),
                            "iterationCount": len(iterations),
                            "expectedStatus": RUN_STATUS_BY_CONCLUSION[spec.conclusion],
                            "expectedStatusByNok": status_by_nok,
                            "expectedConclusion": EXPECTED_CONCLUSION[spec.conclusion],
                            "expectedConclusionReason": expected_reason(
                                spec.conclusion, matrix
                            ),
                            "expectedMatrix": {
                                key: len(matrix.get(key, [])) for key in MATRIX_KEYS
                            },
                            "tags": bublik.get("tags", {}),
                            "requirements": requirements,
                            "verdicts": verdicts,
                            "measurements": measurements,
                            "packages": packages,
                            "logPages": log_pages,
                            "sampleTests": sample_tests_from_matrix(matrix),
                        }
                    ],
                }
            )

        seen_configs: set[tuple[str, str]] = set()
        configs: list[dict[str, Any]] = []
        for fixture in fixtures.values():
            for report_config in getattr(fixture, "report_configs", ()):
                key = (fixture.project, report_config["name"])
                if key in seen_configs:
                    continue
                seen_configs.add(key)
                configs.append(
                    {
                        "project": fixture.project,
                        "type": "report",
                        "name": report_config["name"],
                        "description": report_config.get("description", ""),
                        "content": report_config["content"],
                    }
                )

        manifest = {
            "version": 1,
            "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "baseUrl": settings.base_url,
            "uiBaseUrl": settings.ui_base_url,
            "dashboardUrl": settings.dashboard_url,
            "historyUrl": settings.history_url,
            "importUrl": f"{logs_base}/{urllib.parse.quote(seg)}/",
            "emptyDates": sorted(set(empty_dates)),
            "configs": configs,
            "bundles": bundles,
        }
        # Validate before replacing either published artifact.
        try:
            Manifest.model_validate(manifest)
        except ValidationError as exc:
            raise CliError(
                f"generated manifest failed schema validation:\n{exc}"
            ) from exc

        backup_dir: Path | None = None
        if publication_dir.exists():
            backup_dir = publication_dir.parent / (
                f".{publication_dir.name}.backup-{uuid.uuid4().hex}"
            )
            os.replace(publication_dir, backup_dir)
        try:
            os.replace(staging_dir, publication_dir)
            try:
                write_json(manifest_path, manifest, args.pretty)
            except BaseException:
                _rollback_publication(publication_dir, backup_dir)
                raise
        except BaseException:
            if (
                backup_dir is not None
                and backup_dir.exists()
                and not publication_dir.exists()
            ):
                os.replace(backup_dir, publication_dir)
            raise
        if backup_dir is not None:
            _remove_publication(backup_dir)
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
    if show_summary:
        render_run_summary(manifest, console, title="Generated runs")
    print(str(manifest_path))
