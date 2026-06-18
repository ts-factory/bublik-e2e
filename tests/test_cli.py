from __future__ import annotations

import re

from typer.testing import CliRunner

from cli import app


runner = CliRunner()
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


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
