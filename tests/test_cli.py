from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re

from typer.testing import CliRunner

from cli import app


runner = CliRunner()
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
ONE_DAY = timedelta(days=1).total_seconds()


def visible_output(output: str) -> str:
    return ANSI_RE.sub("", output)


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


def test_generate_validation_failure_exits_non_zero(tmp_path) -> None:
    result = runner.invoke(
        app,
        [
            "generate",
            "--runs",
            "1",
            "--day",
            "2026-04-21:unknown=1",
            "--publish-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert "unknown conclusion" in result.output


def _meta_value(metas: list[dict], name: str) -> str | None:
    for meta in metas:
        if meta.get("name") == name:
            return meta.get("value")
    return None


def _check_node_invariants(node: dict, parent_start: float, prev_finish: float, errs: list[str]) -> float:
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
    result = runner.invoke(
        app,
        [
            "generate",
            "--runs", "3",
            "--fill", "ok",
            "--dates", "2026-04-25",
            "--publish-dir", str(tmp_path),
        ],
    )
    assert result.exit_code == 0, visible_output(result.output)

    bundles = sorted(tmp_path.glob("*/bublik.json"))
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
                assert dt_utc.microsecond % 1000 == 0, f"{node['name']}:{key} not whole-ms"
                wall = datetime.strptime(node[key], "%Y.%m.%d %H:%M:%S.%f")
                assert abs(wall.replace(tzinfo=run_off).timestamp() - utc) < 1e-6
            for child in node.get("iters") or []:
                _check_repr(child)

        _check_repr(root)
        _check_node_invariants(root, root["start_ts_utc"], None, errs)

        # Run boundaries must contain the node tree, with a sub-day gap.
        run_start = datetime.fromisoformat(_meta_value(metas, "START_TIMESTAMP")).timestamp()
        run_finish = datetime.fromisoformat(_meta_value(metas, "FINISH_TIMESTAMP")).timestamp()
        assert run_start <= root["start_ts_utc"], f"{bundle_path.parent.name}: run.start after first node"
        assert root["end_ts_utc"] <= run_finish, f"{bundle_path.parent.name}: last node after run.finish"
        assert run_finish - root["end_ts_utc"] < ONE_DAY

    assert not errs, "\n".join(errs)
