from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re

import pytest
from typer.testing import CliRunner

from cli import app


runner = CliRunner()
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
ONE_DAY = timedelta(days=1).total_seconds()
REPO_ROOT = Path(__file__).resolve().parents[1]
AUTHORITATIVE_SCHEMA_DIR = (
    REPO_ROOT.parent / "bublik-docker" / "bublik" / "bublik" / "data" / "schemas"
)
AUTHORITATIVE_RUN_LOG_SCHEMA = AUTHORITATIVE_SCHEMA_DIR / "run_log.json"
AUTHORITATIVE_META_DATA_SCHEMA = AUTHORITATIVE_SCHEMA_DIR / "meta_data.json"


def visible_output(output: str) -> str:
    return ANSI_RE.sub("", output)


def write_permissive_schema(tmp_path: Path) -> Path:
    schema_path = tmp_path / "run-log.schema.json"
    schema_path.write_text(
        json.dumps(
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "type": "object",
            }
        )
    )
    return schema_path


def test_root_help_lists_commands() -> None:
    result = runner.invoke(app, ["--help"])
    output = visible_output(result.output)

    assert result.exit_code == 0
    assert "generate" in output
    assert "import" in output
    assert "run" in output


def test_generate_help_succeeds() -> None:
    result = runner.invoke(app, ["generate", "--help"])
    output = visible_output(result.output)

    assert result.exit_code == 0
    assert "--publish-dir" in output
    assert "--day" in output
    assert "--run-log-schema" in output
    assert "--meta-data-schema" in output


def test_generation_schema_options_are_not_available_on_import() -> None:
    run_result = runner.invoke(app, ["run", "--help"])
    import_result = runner.invoke(app, ["import", "--help"])

    assert run_result.exit_code == 0
    assert "--run-log-schema" in visible_output(run_result.output)
    assert "--meta-data-sche" in visible_output(run_result.output)
    assert import_result.exit_code == 0
    assert "--run-log-schema" not in visible_output(import_result.output)
    assert "--meta-data-schema" not in visible_output(import_result.output)


def test_generate_validation_failure_exits_non_zero(tmp_path) -> None:
    schema_path = write_permissive_schema(tmp_path)
    result = runner.invoke(
        app,
        [
            "generate",
            "--runs",
            "1",
            "--day",
            "2026-04-21:unknown=1",
            "--publish-dir",
            str(tmp_path / "publish"),
            "--run-log-schema",
            str(schema_path),
            "--meta-data-schema",
            str(schema_path),
        ],
    )

    assert result.exit_code == 1
    assert "unknown conclusion" in result.output


def test_generate_requires_run_log_schema_before_deleting_output(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("BUBLIK_E2E_RUN_LOG_SCHEMA", raising=False)
    publish_dir = tmp_path / "publish"
    publish_dir.mkdir()
    marker = publish_dir / "keep.txt"
    marker.write_text("keep")

    result = runner.invoke(app, ["generate", "--publish-dir", str(publish_dir)])

    assert result.exit_code == 1
    assert "no run-log schema" in visible_output(result.output)
    assert "--run-log-schema" in visible_output(result.output)
    assert marker.read_text() == "keep"


def test_generate_requires_meta_data_schema_before_deleting_output(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("BUBLIK_E2E_META_DATA_SCHEMA", raising=False)
    schema_path = write_permissive_schema(tmp_path)
    publish_dir = tmp_path / "publish"
    publish_dir.mkdir()
    marker = publish_dir / "keep.txt"
    marker.write_text("keep")

    result = runner.invoke(
        app,
        [
            "generate",
            "--publish-dir",
            str(publish_dir),
            "--run-log-schema",
            str(schema_path),
        ],
    )

    assert result.exit_code == 1
    assert "no meta-data schema" in visible_output(result.output)
    assert "--meta-data-schema" in visible_output(result.output)
    assert marker.read_text() == "keep"


def test_generate_rejects_missing_schema_file_before_deleting_output(tmp_path) -> None:
    schema_path = tmp_path / "missing-schema.json"
    meta_data_schema_path = write_permissive_schema(tmp_path)
    publish_dir = tmp_path / "publish"
    publish_dir.mkdir()
    marker = publish_dir / "keep.txt"
    marker.write_text("keep")

    result = runner.invoke(
        app,
        [
            "generate",
            "--publish-dir",
            str(publish_dir),
            "--run-log-schema",
            str(schema_path),
            "--meta-data-schema",
            str(meta_data_schema_path),
        ],
    )

    output = visible_output(result.output)
    assert result.exit_code == 1
    assert "cannot read run-log schema" in output
    assert schema_path.name in output
    assert marker.read_text() == "keep"


def test_generate_rejects_missing_meta_data_schema_before_deleting_output(
    tmp_path,
) -> None:
    run_log_schema_path = write_permissive_schema(tmp_path)
    schema_path = tmp_path / "missing-meta-data-schema.json"
    publish_dir = tmp_path / "publish"
    publish_dir.mkdir()
    marker = publish_dir / "keep.txt"
    marker.write_text("keep")

    result = runner.invoke(
        app,
        [
            "generate",
            "--publish-dir",
            str(publish_dir),
            "--run-log-schema",
            str(run_log_schema_path),
            "--meta-data-schema",
            str(schema_path),
        ],
    )

    output = visible_output(result.output)
    assert result.exit_code == 1
    assert "cannot read meta-data schema" in output
    assert schema_path.name in output
    assert marker.read_text() == "keep"


@pytest.mark.parametrize(
    ("schema_text", "expected"),
    [
        ("{", "malformed run-log schema JSON"),
        ('{"type": 7}', "invalid Draft 7 run-log schema"),
    ],
)
def test_generate_rejects_unusable_schema_before_deleting_output(
    tmp_path, schema_text: str, expected: str
) -> None:
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(schema_text)
    meta_data_schema_path = write_permissive_schema(tmp_path)
    publish_dir = tmp_path / "publish"
    publish_dir.mkdir()
    marker = publish_dir / "keep.txt"
    marker.write_text("keep")

    result = runner.invoke(
        app,
        [
            "generate",
            "--publish-dir",
            str(publish_dir),
            "--run-log-schema",
            str(schema_path),
            "--meta-data-schema",
            str(meta_data_schema_path),
        ],
    )

    output = visible_output(result.output)
    assert result.exit_code == 1
    assert expected in output
    assert schema_path.name in output
    assert marker.read_text() == "keep"


@pytest.mark.parametrize(
    ("schema_text", "expected"),
    [
        ("{", "malformed meta-data schema JSON"),
        ('{"type": 7}', "invalid Draft 7 meta-data schema"),
    ],
)
def test_generate_rejects_unusable_meta_data_schema_before_deleting_output(
    tmp_path, schema_text: str, expected: str
) -> None:
    run_log_schema_path = write_permissive_schema(tmp_path)
    schema_path = tmp_path / "meta-data-schema.json"
    schema_path.write_text(schema_text)
    publish_dir = tmp_path / "publish"
    publish_dir.mkdir()
    marker = publish_dir / "keep.txt"
    marker.write_text("keep")

    result = runner.invoke(
        app,
        [
            "generate",
            "--publish-dir",
            str(publish_dir),
            "--run-log-schema",
            str(run_log_schema_path),
            "--meta-data-schema",
            str(schema_path),
        ],
    )

    output = visible_output(result.output)
    assert result.exit_code == 1
    assert expected in output
    assert schema_path.name in output
    assert marker.read_text() == "keep"


def test_generate_reports_final_bundle_schema_errors(tmp_path) -> None:
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(
        json.dumps(
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "type": "object",
                "properties": {
                    "iters": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"type": {"const": "session"}},
                        },
                    }
                },
            }
        )
    )
    publish_dir = tmp_path / "publish"
    manifest_path = tmp_path / "manifest.json"

    result = runner.invoke(
        app,
        [
            "generate",
            "--runs",
            "1",
            "--fill",
            "ok",
            "--dates",
            "2026-04-25",
            "--publish-dir",
            str(publish_dir),
            "--manifest",
            str(manifest_path),
            "--run-log-schema",
            str(schema_path),
            "--meta-data-schema",
            str(schema_path),
        ],
    )

    output = visible_output(result.output)
    bundle_path = next(publish_dir.glob("*/bublik.json"))
    assert result.exit_code == 1
    assert "failed Draft 7 schema validation" in output
    assert bundle_path.parent.name in output
    assert schema_path.name in output
    assert "/iters/0/type" in output
    assert not manifest_path.exists()


def test_generate_reports_final_meta_data_schema_errors(tmp_path) -> None:
    run_log_schema_path = write_permissive_schema(tmp_path)
    schema_path = tmp_path / "meta-data-schema.json"
    schema_path.write_text(
        json.dumps(
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "type": "object",
                "properties": {"version": {"const": 999}},
            }
        )
    )
    publish_dir = tmp_path / "publish"
    manifest_path = tmp_path / "manifest.json"

    result = runner.invoke(
        app,
        [
            "generate",
            "--runs",
            "1",
            "--fill",
            "ok",
            "--dates",
            "2026-04-25",
            "--publish-dir",
            str(publish_dir),
            "--manifest",
            str(manifest_path),
            "--run-log-schema",
            str(run_log_schema_path),
            "--meta-data-schema",
            str(schema_path),
        ],
    )

    output = visible_output(result.output)
    meta_data_path = next(publish_dir.glob("*/meta_data.json"))
    assert result.exit_code == 1
    assert "generated meta-data failed Draft 7 schema validation" in output
    assert meta_data_path.parent.name in output
    assert schema_path.name in output
    assert "/version" in output
    assert not manifest_path.exists()


def _meta_value(metas: list[dict], name: str) -> str | None:
    for meta in metas:
        if meta.get("name") == name:
            return meta.get("value")
    return None


def _check_node_invariants(
    node: dict, parent_start: float, prev_finish: float, errs: list[str]
) -> float:
    """Assert the ordering invariants fix_result_timestamps relies on.

    Uses *_ts_utc — the float values Bublik imports — and returns this node's
    finish so siblings can be checked against each other.
    """
    name = node.get("name")
    start = node["start_ts_utc"]
    finish = node["end_ts_utc"]
    if finish < start:
        errs.append(f"{name}: finish < start")
    if finish - start >= ONE_DAY:
        errs.append(f"{name}: duration >= 24h")
    if start < parent_start:
        errs.append(f"{name}: start < parent start")
    if prev_finish is not None and start < prev_finish:
        errs.append(f"{name}: start < previous sibling finish")
    child_prev: float | None = None
    for child in node.get("iters") or []:
        child_prev = _check_node_invariants(child, start, child_prev, errs)
    children = node.get("iters") or []
    if children and finish < max(c["end_ts_utc"] for c in children):
        errs.append(f"{name}: finish < max child finish")
    return finish


def test_generated_bundles_satisfy_timestamp_invariants(tmp_path) -> None:
    """Every generated bundle must import cleanly through fix_result_timestamps.

    Regression for the import crash where node *_ts_utc floats and the run
    START/FINISH metas disagreed below the millisecond, and the basic raw log
    carried sub-second clock skew the whole-day-only repair could not fix.
    """
    schema_path = write_permissive_schema(tmp_path)
    publish_dir = tmp_path / "publish"
    result = runner.invoke(
        app,
        [
            "generate",
            "--runs",
            "3",
            "--fill",
            "ok",
            "--dates",
            "2026-04-25",
            "--publish-dir",
            str(publish_dir),
            "--run-log-schema",
            str(schema_path),
            "--meta-data-schema",
            str(schema_path),
        ],
    )
    assert result.exit_code == 0, visible_output(result.output)

    bundles = sorted(publish_dir.glob("*/bublik.json"))
    assert len(bundles) == 3  # basic, dpdk, net-drv

    errs: list[str] = []
    for bundle_path in bundles:
        bundle = json.loads(bundle_path.read_text())
        meta = json.loads((bundle_path.parent / "meta_data.json").read_text())
        metas = meta.get("metas", [])
        root = bundle["iters"][0]

        # String and float representations must agree, at whole-millisecond resolution.
        run_off = datetime.fromisoformat(_meta_value(metas, "START_TIMESTAMP")).tzinfo

        def _check_repr(node: dict) -> None:
            for key in ("start_ts", "end_ts"):
                utc = node[f"{key}_utc"]
                dt_utc = datetime.fromtimestamp(utc, tz=timezone.utc)
                assert dt_utc.microsecond % 1000 == 0, (
                    f"{node['name']}:{key} not whole-ms"
                )
                wall = datetime.strptime(node[key], "%Y.%m.%d %H:%M:%S.%f")
                assert abs(wall.replace(tzinfo=run_off).timestamp() - utc) < 1e-6
            for child in node.get("iters") or []:
                _check_repr(child)

        _check_repr(root)
        _check_node_invariants(root, root["start_ts_utc"], None, errs)

        # Run boundaries must contain the node tree, with a sub-day gap.
        run_start = datetime.fromisoformat(
            _meta_value(metas, "START_TIMESTAMP")
        ).timestamp()
        run_finish = datetime.fromisoformat(
            _meta_value(metas, "FINISH_TIMESTAMP")
        ).timestamp()
        assert run_start <= root["start_ts_utc"], (
            f"{bundle_path.parent.name}: run.start after first node"
        )
        assert root["end_ts_utc"] <= run_finish, (
            f"{bundle_path.parent.name}: last node after run.finish"
        )
        assert run_finish - root["end_ts_utc"] < ONE_DAY

    assert not errs, "\n".join(errs)


@pytest.mark.skipif(
    not AUTHORITATIVE_RUN_LOG_SCHEMA.is_file()
    or not AUTHORITATIVE_META_DATA_SCHEMA.is_file(),
    reason="authoritative Bublik schemas checkout is unavailable",
)
def test_all_providers_and_conclusions_validate_against_authoritative_schemas(
    tmp_path,
) -> None:
    publish_dir = tmp_path / "publish"
    conclusions = (
        "ok",
        "nok-warning",
        "nok-error",
        "warning",
        "error",
        "running",
        "busy",
        "stopped",
        "interrupted",
        "compromised",
    )
    day_spec = ",".join(f"{conclusion}=1" for conclusion in conclusions)

    result = runner.invoke(
        app,
        [
            "generate",
            "--day",
            f"2026-04-25:{day_spec}",
            "--publish-dir",
            str(publish_dir),
            "--run-log-schema",
            str(AUTHORITATIVE_RUN_LOG_SCHEMA),
            "--meta-data-schema",
            str(AUTHORITATIVE_META_DATA_SCHEMA),
        ],
    )

    assert result.exit_code == 0, visible_output(result.output)
    bublik_paths = list(publish_dir.glob("*/bublik.json"))
    meta_data_paths = list(publish_dir.glob("*/meta_data.json"))
    assert len(bublik_paths) == 30
    assert len(meta_data_paths) == 30
    for meta_data_path in meta_data_paths:
        meta_data = json.loads(meta_data_path.read_text())
        e2e_run_id = next(
            meta for meta in meta_data["metas"] if meta["name"] == "E2E_RUN_ID"
        )
        assert "type" not in e2e_run_id
