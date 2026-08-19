#!/usr/bin/env python3

"""Generate, publish, and import deterministic Bublik fixture runs.

Installed as the ``bublik-e2e`` command (``bublik-e2e <command> [options]``).
The tool is instance-agnostic: it targets any Bublik instance through ``--url``
and the admin credentials.

Bundled fixtures: basic, dpdk-ethdev-ts, net-drv-ts.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Annotated, Callable, List, Optional

import typer
from rich.table import Table

from core.common import CliError, console
from core.discovery import selected_fixtures
from core.importer import generate_and_import, import_manifest
from core.manifest import generate_manifest
from core.manifest_models import manifest_json_schema
from core.plan_models import plan_json_schema
from core.plan_file import load_plan_file
from core.planning import build_mixes, build_plan

app = typer.Typer(
    rich_markup_mode="rich",
    no_args_is_help=True,
    add_completion=False,
    help=__doc__,
)

# ---------------------------------------------------------------------------
# Option type aliases, grouped the same way as the old add_*_arguments helpers.
# Each command composes the groups it needs; defaults are set at the call site
# so a single alias can serve both required and optional positions.
# ---------------------------------------------------------------------------

# Connection
UrlOpt = Annotated[
    Optional[str],
    typer.Option(
        help="Base URL of the target Bublik instance "
        "(overrides BUBLIK_FQDN/BUBLIK_DOCKER_PROXY_PORT/URL_PREFIX).",
    ),
]
EnvFileOpt = Annotated[
    Optional[Path],
    typer.Option(help="Optional .env file to seed environment values from."),
]
ManifestOpt = Annotated[
    Optional[Path],
    typer.Option(help="Manifest file path (default: ./.e2e/e2e-manifest.json)."),
]

# Generation
FixtureOpt = Annotated[
    Optional[List[str]],
    typer.Option(
        metavar="DIR",
        help="Fixture provider directory (containing fixture.py). Repeatable. "
        "Omit to auto-discover all bundled providers.",
    ),
]
RunsOpt = Annotated[
    Optional[int],
    typer.Option(
        help="Total number of runs to produce. Optional with --day (derived "
        "from the day specs; if given, asserted to match). Required with --fill, "
        "where it drives the loop count.",
    ),
]
DayOpt = Annotated[
    Optional[List[str]],
    typer.Option(
        metavar="YYYY-MM-DD:SPEC",
        help="Runs for one date. SPEC is a comma list of "
        "\\[fixture.]conclusion\\[@mix]\\[+ui]=count. A trailing +ui marks the "
        "runs for import through the UI (Playwright) instead of the CLI API "
        "import. Conclusions: ok, nok-warning, "
        "nok-error, warning, error, running, busy, stopped, interrupted, "
        "compromised. An unprefixed conclusion applies to every discovered "
        "fixture; prefix with a fixture name (e.g. dpdk-ethdev-ts.ok) to scope "
        "it. @mix is either a named --mix or an inline definition "
        "key=val;key=val (e.g. @unexpectedFailed=20%;unexpectedSkipped=5%). "
        "Repeatable. Mutually exclusive with --fill/--dates.",
    ),
]
FillOpt = Annotated[
    Optional[str],
    typer.Option(
        metavar="CONCLUSION",
        help="Generate --runs runs of this conclusion, round-robining over "
        "fixtures and --dates. Requires --dates; excludes --day.",
    ),
]
DatesOpt = Annotated[
    Optional[str],
    typer.Option(
        metavar="YYYY-MM-DD[..YYYY-MM-DD]",
        help="Single date or inclusive range used by --fill.",
    ),
]
PlanOpt = Annotated[
    Optional[Path],
    typer.Option(
        metavar="FILE",
        help="JSON plan file holding the campaign: "
        '{"version": 1, "runs": N, "mixes": {...}, "days": {...}}. Equivalent to '
        "the matching --runs/--mix/--day options, and validated the same way. "
        "Day and mix specs may be one string or a list of strings. Mutually "
        "exclusive with --day/--fill; extra --mix options are merged in.",
    ),
]
MixOpt = Annotated[
    Optional[List[str]],
    typer.Option(
        metavar="NAME:k=v,...",
        help="Define a named result mix, e.g. "
        "'warning-mix:unexpectedFailed=20%,unexpectedSkipped=5%', then reference "
        "it from a --day spec with @warning-mix. Repeatable. (For a one-off, "
        "skip this and inline the mix on the --day spec.)",
    ),
]
PublishDirOpt = Annotated[
    Optional[Path],
    typer.Option(
        help="Full path bundles are written to; the instance must serve it at "
        "{url}/logs/<name>/ (its basename becomes the URL segment). Required "
        "unless BUBLIK_E2E_PUBLISH_DIR is set.",
    ),
]
PrettyOpt = Annotated[
    bool,
    typer.Option(help="Pretty-print generated JSON (indented, sorted keys)."),
]
RunLogSchemaOpt = Annotated[
    Optional[Path],
    typer.Option(
        help="Draft 7 JSON Schema used to validate each finalized bublik.json. "
        "Overrides BUBLIK_E2E_RUN_LOG_SCHEMA; otherwise derived from "
        "BUBLIK_DJANGO_ROOT."
    ),
]
MetaDataSchemaOpt = Annotated[
    Optional[Path],
    typer.Option(
        "--meta-data-schema",
        help="Draft 7 JSON Schema used to validate each finalized meta_data.json. "
        "Overrides BUBLIK_E2E_META_DATA_SCHEMA; otherwise derived from "
        "BUBLIK_DJANGO_ROOT.",
    ),
]

# Auth
EmailOpt = Annotated[
    Optional[str],
    typer.Option(help="Admin email for API login (overrides DJANGO_SUPERUSER_EMAIL)."),
]
PasswordOpt = Annotated[
    Optional[str],
    typer.Option(
        help="Admin password for API login (overrides DJANGO_SUPERUSER_PASSWORD).",
    ),
]
SetupProjectsOpt = Annotated[
    bool,
    typer.Option(
        help="Create any missing projects and the per-project 'references', "
        "'meta', and 'per_conf' configs before importing.",
    ),
]
TimeoutOpt = Annotated[
    int,
    typer.Option(
        help="Seconds to wait with no import progress before giving up. The "
        "deadline resets on each completed or advancing run, so large batches "
        "that keep moving never time out."
    ),
]
IncludeUiOpt = Annotated[
    bool,
    typer.Option(
        "--include-ui",
        help="Also import bundles marked importVia=ui (normally left for the "
        "Playwright suite to import through the UI).",
    ),
]


GENERATE_EPILOG = """[bold]Examples[/]

[dim]These examples assume BUBLIK_DJANGO_ROOT points to the Bublik Django repository.[/]

[dim]Per-fixture day spec (run count is derived, no --runs needed):[/]

[cyan]bublik-e2e generate --day "2026-04-21:basic.ok=2,dpdk-ethdev-ts.nok-warning=1" --publish-dir /srv/logs/e2e[/]

[dim]Unprefixed conclusions apply to every discovered fixture:[/]

[cyan]bublik-e2e generate --day "2026-04-21:ok=1,warning=1,error=1" --publish-dir /srv/logs/e2e[/]

[dim]Inline mix on a day spec (no pre-defined --mix):[/]

[cyan]bublik-e2e generate --day "2026-04-21:net-drv-ts.nok-warning@unexpectedFailed=20%;unexpectedSkipped=5%=2" --publish-dir /srv/logs/e2e[/]

[dim]Named mix reused across day specs:[/]

[cyan]bublik-e2e generate --mix "warning-mix:unexpectedFailed=20%,unexpectedSkipped=5%" --day "2026-04-21:dpdk-ethdev-ts.nok-warning@warning-mix=2" --publish-dir /srv/logs/e2e[/]

[dim]Fill a whole month with one conclusion (--runs drives the loop):[/]

[cyan]bublik-e2e generate --runs 100 --fill ok --dates "2026-04-01..2026-04-30" --publish-dir /srv/logs/e2e[/]
"""

IMPORT_EPILOG = """[bold]Examples[/]

[dim]Import the default manifest, creating any missing projects first:[/]

[cyan]bublik-e2e import --url http://localhost --setup-projects[/]

[dim]Import a specific manifest against a remote instance:[/]

[cyan]bublik-e2e import --manifest ./.e2e/e2e-manifest.json --url https://bublik.example.com --email admin@bublik.com --password admin[/]
"""

RUN_EPILOG = """[bold]Examples[/]

[dim]These examples assume BUBLIK_DJANGO_ROOT points to the Bublik Django repository.[/]

[dim]Per-fixture day spec (run count is derived, no --runs needed):[/]

[cyan]bublik-e2e run --day "2026-04-21:basic.ok=2,dpdk-ethdev-ts.nok-warning=1" --publish-dir /srv/logs/e2e --url http://localhost[/]

[dim]Inline mix on a day spec (no pre-defined --mix):[/]

[cyan]bublik-e2e run --day "2026-04-21:net-drv-ts.nok-warning@unexpectedFailed=20%;unexpectedSkipped=5%=2" --publish-dir /srv/logs/e2e --url http://localhost[/]

[dim]Multi-day campaign across fixtures, creating projects on the way in:[/]

[cyan]bublik-e2e run --setup-projects --day "2026-04-21:basic.ok=2,basic.nok-error=1" --day "2026-04-22:dpdk-ethdev-ts.ok=2,dpdk-ethdev-ts.nok-warning=1" --publish-dir /srv/logs/e2e --url http://localhost[/]

[dim]Fill a whole month with one conclusion (--runs drives the loop):[/]

[cyan]bublik-e2e run --runs 100 --fill ok --dates "2026-04-01..2026-04-30" --setup-projects --publish-dir /srv/logs/e2e --url http://localhost[/]
"""


def _dispatch(func: Callable[[argparse.Namespace], None], **params: object) -> None:
    """Hand a real argparse.Namespace to a core entry point and surface errors."""
    try:
        func(argparse.Namespace(**params))
    except CliError as exc:
        console.print(f"[bold red]error:[/] {exc}", soft_wrap=True)
        raise typer.Exit(code=1)


def _plan_or_exit(
    plan: Optional[Path],
    runs: Optional[int],
    day: Optional[List[str]],
    fill: Optional[str],
    mix: Optional[List[str]],
) -> tuple[Optional[int], List[str], List[str]]:
    """Fold a --plan file into the (runs, day, mix) triple the core expects."""
    if plan is None:
        return runs, day or [], mix or []
    try:
        if day:
            raise CliError("--plan and --day are mutually exclusive")
        if fill:
            raise CliError("--plan and --fill are mutually exclusive")
        plan_runs, plan_mix, plan_day = load_plan_file(plan)
    except CliError as exc:
        console.print(f"[bold red]error:[/] {exc}", soft_wrap=True)
        raise typer.Exit(code=1)
    # An explicit --runs still wins, so a plan can be spot-checked from the CLI;
    # extra --mix definitions are additive and may override a plan's mix.
    return (runs if runs is not None else plan_runs, plan_day, plan_mix + (mix or []))


@app.command(epilog=GENERATE_EPILOG)
def generate(
    runs: RunsOpt = None,
    fixture: FixtureOpt = None,
    plan: PlanOpt = None,
    day: DayOpt = None,
    fill: FillOpt = None,
    dates: DatesOpt = None,
    mix: MixOpt = None,
    publish_dir: PublishDirOpt = None,
    pretty: PrettyOpt = False,
    run_log_schema: RunLogSchemaOpt = None,
    meta_data_schema: MetaDataSchemaOpt = None,
    url: UrlOpt = None,
    env_file: EnvFileOpt = None,
    manifest: ManifestOpt = None,
) -> None:
    """Generate bundles into --publish-dir and write the manifest. No import."""
    runs, day, mix = _plan_or_exit(plan, runs, day, fill, mix)
    _dispatch(
        generate_manifest,
        url=url,
        env_file=env_file,
        manifest=manifest,
        fixture=fixture or [],
        runs=runs,
        day=day,
        fill=fill,
        dates=dates,
        mix=mix,
        publish_dir=publish_dir,
        pretty=pretty,
        run_log_schema=run_log_schema,
        meta_data_schema=meta_data_schema,
    )


@app.command(name="import", epilog=IMPORT_EPILOG)
def import_(
    url: UrlOpt = None,
    env_file: EnvFileOpt = None,
    manifest: ManifestOpt = None,
    email: EmailOpt = None,
    password: PasswordOpt = None,
    setup_projects: SetupProjectsOpt = False,
    timeout: TimeoutOpt = 600,
    include_ui: IncludeUiOpt = False,
) -> None:
    """Import an existing manifest into the instance via the API."""
    _dispatch(
        import_manifest,
        url=url,
        env_file=env_file,
        manifest=manifest,
        email=email,
        password=password,
        setup_projects=setup_projects,
        timeout=timeout,
        include_ui=include_ui,
    )


@app.command(epilog=RUN_EPILOG)
def run(
    runs: RunsOpt = None,
    fixture: FixtureOpt = None,
    plan: PlanOpt = None,
    day: DayOpt = None,
    fill: FillOpt = None,
    dates: DatesOpt = None,
    mix: MixOpt = None,
    publish_dir: PublishDirOpt = None,
    pretty: PrettyOpt = False,
    run_log_schema: RunLogSchemaOpt = None,
    meta_data_schema: MetaDataSchemaOpt = None,
    url: UrlOpt = None,
    env_file: EnvFileOpt = None,
    manifest: ManifestOpt = None,
    email: EmailOpt = None,
    password: PasswordOpt = None,
    setup_projects: SetupProjectsOpt = False,
    timeout: TimeoutOpt = 600,
    include_ui: IncludeUiOpt = False,
) -> None:
    """Generate bundles and import them in one shot (generate + import)."""
    runs, day, mix = _plan_or_exit(plan, runs, day, fill, mix)
    _dispatch(
        generate_and_import,
        url=url,
        env_file=env_file,
        manifest=manifest,
        fixture=fixture or [],
        runs=runs,
        day=day,
        fill=fill,
        dates=dates,
        mix=mix,
        publish_dir=publish_dir,
        pretty=pretty,
        run_log_schema=run_log_schema,
        meta_data_schema=meta_data_schema,
        email=email,
        password=password,
        setup_projects=setup_projects,
        timeout=timeout,
        include_ui=include_ui,
    )


PLAN_EPILOG = """[bold]Examples[/]

[dim]Validate the versioned campaign and see what it expands to:[/]

[cyan]bublik-e2e plan --plan e2e/plan.json[/]

[dim]Break the summary down by fixture instead of by date:[/]

[cyan]bublik-e2e plan --plan e2e/plan.json --by fixture[/]
"""


@app.command(epilog=PLAN_EPILOG)
def plan(
    plan: PlanOpt = None,
    fixture: FixtureOpt = None,
    day: DayOpt = None,
    mix: MixOpt = None,
    runs: RunsOpt = None,
    by: Annotated[
        str,
        typer.Option(help="Group the summary by 'date', 'fixture' or 'conclusion'."),
    ] = "date",
) -> None:
    """Expand a plan and print what it would generate. Nothing is written."""
    groups = {"date", "fixture", "conclusion"}
    if by not in groups:
        console.print(
            f"[bold red]error:[/] --by must be one of: {', '.join(sorted(groups))}"
        )
        raise typer.Exit(code=1)
    runs, day, mix = _plan_or_exit(plan, runs, day, None, mix)
    try:
        args = argparse.Namespace(
            fixture=fixture or [], runs=runs, day=day, fill=None, dates=None, mix=mix
        )
        fixtures = selected_fixtures(args)
        mixes = build_mixes(args)
        planned, empty_dates = build_plan(args, fixtures, mixes)
    except CliError as exc:
        console.print(f"[bold red]error:[/] {exc}", soft_wrap=True)
        raise typer.Exit(code=1)

    key = {
        "date": lambda run: run.run_date,
        "fixture": lambda run: run.fixture.name,
        "conclusion": lambda run: run.conclusion,
    }[by]
    table = Table(title=f"{len(planned)} runs by {by}", title_justify="left")
    table.add_column(by.capitalize())
    table.add_column("Runs", justify="right")
    table.add_column("Via UI", justify="right")
    for value in sorted({key(run) for run in planned}):
        rows = [run for run in planned if key(run) == value]
        via_ui = sum(1 for run in rows if run.import_via == "ui")
        table.add_row(value, str(len(rows)), str(via_ui) if via_ui else "-")
    console.print(table)
    console.print(
        f"{len(planned)} runs, "
        f"{len({run.run_date for run in planned})} dates with runs, "
        f"{len(empty_dates)} empty, "
        f"{sum(1 for run in planned if run.import_via == 'ui')} imported through the UI"
    )


@app.command()
def schema(
    out: Annotated[
        Optional[Path],
        typer.Option(help="Write the schema to this file instead of stdout."),
    ] = None,
    kind: Annotated[
        str,
        typer.Option(
            help="Which schema to print: 'manifest' (default, drives the UI's "
            "TypeScript codegen) or 'plan' (drives editor completion and "
            "validation of plan.yaml)."
        ),
    ] = "manifest",
) -> None:
    """Print a JSON Schema (manifest by default; --kind plan for plan files)."""
    builders = {"manifest": manifest_json_schema, "plan": plan_json_schema}
    if kind not in builders:
        console.print(
            f"[bold red]error:[/] --kind must be one of: {', '.join(sorted(builders))}"
        )
        raise typer.Exit(code=1)
    rendered = json.dumps(builders[kind](), indent=2, sort_keys=True) + "\n"
    if out is None:
        print(rendered, end="")
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rendered, encoding="utf-8")
    print(str(out))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
