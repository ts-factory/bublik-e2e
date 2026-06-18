"""Render a human-readable summary table of the runs in a manifest.

Shown before generation/import so the operator can eyeball each run's expected
conclusion and its expected/unexpected result breakdown — mirroring the columns
of the Bublik run tree (expected ✓ vs unexpected ✗ passed/failed/skipped, plus
abnormal results).
"""

from __future__ import annotations

from typing import Any

from rich.box import ROUNDED
from rich.console import Console
from rich.table import Table

# conclusionSpec -> (label, Rich style) for the colored CONCLUSION cell.
_CONCLUSION_STYLES: dict[str, tuple[str, str]] = {
    "ok": ("OK", "bold green"),
    "nok-warning": ("NOK-WARNING", "yellow"),
    "nok-error": ("NOK-ERROR", "red"),
    "warning": ("WARNING", "bold yellow"),
    "error": ("ERROR", "bold red"),
    "running": ("RUNNING", "cyan"),
    "busy": ("BUSY", "cyan"),
    "stopped": ("STOPPED", "magenta"),
    "interrupted": ("INTERRUPTED", "bold red"),
    "compromised": ("COMPROMISED", "bold red"),
}


def _conclusion_cell(spec: str) -> str:
    label, style = _CONCLUSION_STYLES.get(spec, (spec.upper(), "white"))
    return f"[{style}]{label}[/]"


def _count(value: int, style: str) -> str:
    """Dim zeros, color non-zero counts so the eye lands on real results."""
    if not value:
        return "[dim]0[/]"
    return f"[{style}]{value}[/]"


def render_run_summary(
    manifest: dict[str, Any], console: Console, *, title: str = "Runs"
) -> None:
    bundles = manifest.get("bundles", [])
    if not bundles:
        return

    table = Table(
        title=f"{title} — {len(bundles)} run(s)",
        caption="[green]✓[/] expected  [red]✗[/] unexpected  •  "
        "P passed  F failed  S skipped  ABN abnormal",
        box=ROUNDED,
        header_style="bold",
        title_style="bold",
        caption_style="dim",
        expand=False,
    )
    # Compact numeric headers keep the table readable on normal terminals:
    # ✓ = expected, ✗ = unexpected; P/F/S = passed/failed/skipped; ABN = abnormal.
    table.add_column("RUN", style="bold", no_wrap=True)
    table.add_column("DATE", no_wrap=True)
    table.add_column("CONCLUSION", no_wrap=True)
    table.add_column("TOTAL", justify="right")
    table.add_column("✓P", justify="right", header_style="green")
    table.add_column("✓F", justify="right", header_style="green")
    table.add_column("✓S", justify="right", header_style="green")
    table.add_column("✗P", justify="right", header_style="red")
    table.add_column("✗F", justify="right", header_style="red")
    table.add_column("✗S", justify="right", header_style="red")
    table.add_column("ABN", justify="right", header_style="yellow")

    totals = {key: 0 for key in (
        "iter", "eP", "eF", "eS", "uP", "uF", "uS", "abn",
    )}

    for bundle in bundles:
        expected = (bundle.get("expectedRuns") or [{}])[0]
        matrix = expected.get("expectedMatrix", {})
        iterations = expected.get("iterationCount", 0)
        e_pass = matrix.get("expectedPassed", 0)
        e_fail = matrix.get("expectedFailed", 0)
        e_skip = matrix.get("expectedSkipped", 0)
        u_pass = matrix.get("unexpectedPassed", 0)
        u_fail = matrix.get("unexpectedFailed", 0)
        u_skip = matrix.get("unexpectedSkipped", 0)
        abnormal = matrix.get("abnormal", 0)

        totals["iter"] += iterations
        totals["eP"] += e_pass
        totals["eF"] += e_fail
        totals["eS"] += e_skip
        totals["uP"] += u_pass
        totals["uF"] += u_fail
        totals["uS"] += u_skip
        totals["abn"] += abnormal

        table.add_row(
            str(bundle.get("id", "")),
            str(bundle.get("date", "")),
            _conclusion_cell(str(bundle.get("conclusionSpec", ""))),
            f"[bold]{iterations}[/]",
            _count(e_pass, "green"),
            _count(e_fail, "green"),
            _count(e_skip, "green"),
            _count(u_pass, "red"),
            _count(u_fail, "red"),
            _count(u_skip, "red"),
            _count(abnormal, "yellow"),
        )

    if len(bundles) > 1:
        table.add_row(
            "[bold]TOTAL[/]",
            "",
            "",
            f"[bold]{totals['iter']}[/]",
            _count(totals["eP"], "green"),
            _count(totals["eF"], "green"),
            _count(totals["eS"], "green"),
            _count(totals["uP"], "red"),
            _count(totals["uF"], "red"),
            _count(totals["uS"], "red"),
            _count(totals["abn"], "yellow"),
            end_section=True,
        )

    console.print(table)
