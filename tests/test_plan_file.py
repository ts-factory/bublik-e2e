from __future__ import annotations

import json
from pathlib import Path
import re

import pytest
from typer.testing import CliRunner

from cli import app
from core.common import CliError
from core.discovery import discover_fixtures
from core.plan_file import load_plan, load_plan_file

runner = CliRunner()
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

MINIMAL = """
version: 1
days:
  2026-04-20:
    - basic.ok=1
"""


def visible_output(output: str) -> str:
    return ANSI_RE.sub("", output)


def write_plan(tmp_path: Path, content: str, name: str = "plan.yaml") -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_days_accept_a_list_or_a_string(tmp_path: Path) -> None:
    as_list = write_plan(
        tmp_path,
        """
version: 1
days:
  2026-04-20:
    - basic.ok=1
    - net-drv-ts.warning=2
""",
        "list.yaml",
    )
    as_string = write_plan(
        tmp_path,
        """
version: 1
days:
  2026-04-20: "basic.ok=1,net-drv-ts.warning=2"
""",
        "string.yaml",
    )
    assert load_plan_file(as_list) == load_plan_file(as_string)


def test_mixes_accept_a_mapping_or_a_string(tmp_path: Path) -> None:
    as_mapping = write_plan(
        tmp_path,
        """
version: 1
mixes:
  warn:
    unexpectedFailed: 20%
    expectedKilled: 1
days:
  2026-04-20:
    - basic.ok@warn=1
""",
        "mapping.yaml",
    )
    as_string = write_plan(
        tmp_path,
        """
version: 1
mixes:
  warn: "unexpectedFailed=20%,expectedKilled=1"
days:
  2026-04-20:
    - basic.ok@warn=1
""",
        "string.yaml",
    )
    assert load_plan_file(as_mapping) == load_plan_file(as_string)
    assert load_plan_file(as_mapping)[1] == [
        "warn:unexpectedFailed=20%,expectedKilled=1"
    ]


def test_json_plans_still_load(tmp_path: Path) -> None:
    """JSON is valid YAML, so an unmigrated .json plan keeps working."""
    path = tmp_path / "plan.json"
    path.write_text(
        json.dumps({"version": 1, "days": {"2026-04-20": "basic.ok=1"}}),
        encoding="utf-8",
    )
    assert load_plan_file(path) == (None, [], ["2026-04-20:basic.ok=1"])


def test_days_are_emitted_oldest_first(tmp_path: Path) -> None:
    path = write_plan(
        tmp_path,
        """
version: 1
days:
  2026-04-22:
    - basic.ok=1
  2026-04-20:
    - basic.ok=1
""",
    )
    _, _, days = load_plan_file(path)
    assert days == ["2026-04-20:basic.ok=1", "2026-04-22:basic.ok=1"]


def test_empty_day_is_kept_as_a_planned_empty_date(tmp_path: Path) -> None:
    path = write_plan(
        tmp_path,
        """
version: 1
days:
  2026-04-19: []
  2026-04-20:
    - basic.ok=1
""",
    )
    _, _, days = load_plan_file(path)
    assert days == ["2026-04-19:", "2026-04-20:basic.ok=1"]


def test_duplicate_day_is_rejected(tmp_path: Path) -> None:
    path = write_plan(
        tmp_path,
        """
version: 1
days:
  2026-04-20:
    - basic.ok=1
  2026-04-20:
    - basic.ok=2
""",
    )
    with pytest.raises(CliError, match="duplicate key 2026-04-20"):
        load_plan(path)


def test_duplicate_mix_is_rejected(tmp_path: Path) -> None:
    path = write_plan(
        tmp_path,
        """
version: 1
mixes:
  warn: "unexpectedFailed=20%"
  warn: "unexpectedFailed=30%"
days:
  2026-04-20:
    - basic.ok@warn=1
""",
    )
    with pytest.raises(CliError, match="duplicate key 'warn'"):
        load_plan(path)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("version: 2\ndays:\n  2026-04-20: []\n", "version"),
        ("version: 1\ndayz:\n  2026-04-20: []\n", "Extra inputs are not permitted"),
        ("version: 1\n", "days"),
        ("version: 1\nruns: 0\ndays:\n  2026-04-20: []\n", "greater than or equal"),
        ('version: 1\ndays:\n  "2026-04-31": []\n', "valid date"),
        ("version: 1\nmixes:\n  9bad: x=1\ndays:\n  2026-04-20: []\n", "9bad"),
    ],
)
def test_invalid_plans_are_rejected(tmp_path: Path, content: str, message: str) -> None:
    path = write_plan(tmp_path, content)
    with pytest.raises(CliError, match=message):
        load_plan(path)


def test_empty_and_unreadable_files_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(CliError, match="is empty"):
        load_plan(write_plan(tmp_path, "\n"))
    with pytest.raises(CliError, match="cannot read plan file"):
        load_plan(tmp_path / "missing.yaml")
    with pytest.raises(CliError, match="invalid YAML"):
        load_plan(write_plan(tmp_path, "version: 1\ndays: [\n"))


def test_plan_command_summarizes_the_campaign(tmp_path: Path) -> None:
    path = write_plan(
        tmp_path,
        """
version: 1
runs: 4
days:
  2026-04-19: []
  2026-04-20:
    - basic.ok=1
    - basic.ok+ui=1
  2026-04-21:
    - net-drv-ts.warning=2
""",
    )
    result = runner.invoke(app, ["plan", "--plan", str(path)])
    assert result.exit_code == 0, result.output
    output = visible_output(result.output)
    assert "4 runs" in output
    assert "2 dates with runs, 1 empty" in output
    assert "1 imported through the UI" in output


def test_plan_command_groups_by_fixture(tmp_path: Path) -> None:
    path = write_plan(
        tmp_path,
        """
version: 1
days:
  2026-04-20:
    - basic.ok=1
    - net-drv-ts.ok=2
""",
    )
    result = runner.invoke(app, ["plan", "--plan", str(path), "--by", "fixture"])
    assert result.exit_code == 0, result.output
    assert "net-drv-ts" in visible_output(result.output)


def test_plan_command_rejects_an_unknown_grouping(tmp_path: Path) -> None:
    path = write_plan(tmp_path, MINIMAL)
    result = runner.invoke(app, ["plan", "--plan", str(path), "--by", "nope"])
    assert result.exit_code == 1
    assert "--by must be one of" in visible_output(result.output)


def test_declared_run_count_is_asserted(tmp_path: Path) -> None:
    """The plan's `runs:` is the guard rail against an accidental edit."""
    path = write_plan(
        tmp_path,
        """
version: 1
runs: 5
days:
  2026-04-20:
    - basic.ok=1
""",
    )
    result = runner.invoke(app, ["plan", "--plan", str(path)])
    assert result.exit_code == 1
    assert "--runs=5 but fixture plan contains 1 runs" in visible_output(result.output)


@pytest.mark.parametrize("command", ["generate", "run", "plan"])
def test_plan_and_day_are_mutually_exclusive(tmp_path: Path, command: str) -> None:
    path = write_plan(tmp_path, MINIMAL)
    result = runner.invoke(
        app, [command, "--plan", str(path), "--day", "2026-04-20:basic.ok=1"]
    )
    assert result.exit_code == 1
    assert "--plan and --day are mutually exclusive" in visible_output(result.output)


def test_plan_and_fill_are_mutually_exclusive(tmp_path: Path) -> None:
    path = write_plan(tmp_path, MINIMAL)
    result = runner.invoke(
        app, ["generate", "--plan", str(path), "--fill", "ok", "--dates", "2026-04-20"]
    )
    assert result.exit_code == 1
    assert "--plan and --fill are mutually exclusive" in visible_output(result.output)


def test_command_line_mixes_are_merged_into_the_plan(tmp_path: Path) -> None:
    """An extra --mix lets a plan be tweaked without editing the file."""
    path = write_plan(
        tmp_path,
        """
version: 1
mixes:
  warn: "unexpectedFailed=20%"
days:
  2026-04-20:
    - basic.ok@extra=1
""",
    )
    result = runner.invoke(
        app,
        ["plan", "--plan", str(path), "--mix", "extra:unexpectedFailed=50%"],
    )
    assert result.exit_code == 0, result.output
    assert "1 runs" in visible_output(result.output)


def test_schema_kind_plan_is_exportable() -> None:
    result = runner.invoke(app, ["schema", "--kind", "plan"])
    assert result.exit_code == 0, result.output
    schema = json.loads(result.stdout)
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {"version", "runs", "mixes", "days"}
    assert "days" in schema["required"]


def test_schema_rejects_an_unknown_kind() -> None:
    result = runner.invoke(app, ["schema", "--kind", "nope"])
    assert result.exit_code == 1
    assert "--kind must be one of" in visible_output(result.output)


def test_bundled_fixtures_keep_their_projects() -> None:
    """Guards the fixture->project mapping bublik-docker used to re-assert."""
    fixtures = discover_fixtures()
    assert {name: fixture.project for name, fixture in fixtures.items()} == {
        "basic": "bublik-e2e",
        "dpdk-ethdev-ts": "tsf/dpdk-ethdev",
        "net-drv-ts": "tsf/net-drv",
    }
