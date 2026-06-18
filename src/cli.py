#!/usr/bin/env python3

"""Generate, publish, and import deterministic Bublik fixture runs.

Installed as the ``bublik-e2e`` command (``bublik-e2e <command> [options]``).
The tool is instance-agnostic: it targets any Bublik instance through ``--url``
and the admin credentials.

Bundled fixtures: basic, dpdk-ethdev-ts, net-drv-ts.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Annotated, Callable, List, Optional

import typer

from core.common import CliError, console
from core.importer import generate_and_import, import_manifest
from core.manifest import generate_manifest

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
    int,
    typer.Option(
        help="Total number of runs to produce. Must equal the runs implied by "
        "--day, or drive the loop count in --fill mode.",
    ),
]
DayOpt = Annotated[
    Optional[List[str]],
    typer.Option(
        metavar="YYYY-MM-DD:SPEC",
        help="Runs for one date. SPEC is a comma list of "
        "\\[fixture.]conclusion\\[@mix]=count. Conclusions: ok, nok-warning, "
        "nok-error, warning, error, running, busy, stopped, interrupted, "
        "compromised. An unprefixed conclusion applies to EVERY discovered "
        "fixture (so --runs must equal count x fixtures); prefix with a fixture "
        "name to scope it (basic, dpdk-ethdev-ts, net-drv-ts). Attach a named "
        "--mix with @mix to control unexpected ratios. Repeatable. Mutually "
        "exclusive with --fill/--dates.",
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
MixOpt = Annotated[
    Optional[List[str]],
    typer.Option(
        metavar="NAME k=v,...",
        help="Define a named result mix, e.g. "
        "'warning-mix unexpectedFailed=20%,unexpectedSkipped=5%'. Repeatable.",
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
        help="Create any missing projects and the per-project 'references' "
        "config before importing.",
    ),
]
TimeoutOpt = Annotated[
    int,
    typer.Option(help="Seconds to wait for the import job to finish."),
]


GENERATE_EPILOG = """[bold]Examples[/]

[dim]All bundled fixtures — counts apply per fixture (3 conclusions x 3 fixtures = 9):[/]

[cyan]bublik-e2e generate --runs 9 --day "2026-04-21:ok=1,warning=1,error=1" --publish-dir /srv/logs/e2e[/]

[dim]Scope to one fixture so the plan matches --runs exactly:[/]

[cyan]bublik-e2e generate --runs 10 --day "2026-04-21:basic.ok=7,basic.warning=2,basic.error=1" --publish-dir /srv/logs/e2e[/]

[dim]DPDK runs with NOK (unexpected) results:[/]

[cyan]bublik-e2e generate --runs 5 --day "2026-04-21:dpdk-ethdev-ts.ok=3,dpdk-ethdev-ts.nok-warning=1,dpdk-ethdev-ts.nok-error=1" --publish-dir /srv/logs/e2e[/]

[dim]net-drv NOK runs with an explicit unexpected percentage mix:[/]

[cyan]bublik-e2e generate --runs 4 --mix "warning-mix unexpectedFailed=20%,unexpectedSkipped=5%" --day "2026-04-21:net-drv-ts.ok=2,net-drv-ts.nok-warning@warning-mix=2" --publish-dir /srv/logs/e2e[/]

[dim]Fill a whole month with one conclusion:[/]

[cyan]bublik-e2e generate --runs 100 --fill ok --dates "2026-04-01..2026-04-30" --publish-dir /srv/logs/e2e[/]
"""

IMPORT_EPILOG = """[bold]Examples[/]

[dim]Import the default manifest, creating any missing projects first:[/]

[cyan]bublik-e2e import --url http://localhost --setup-projects[/]

[dim]Import a specific manifest against a remote instance:[/]

[cyan]bublik-e2e import --manifest ./.e2e/e2e-manifest.json --url https://bublik.example.com --email admin@bublik.com --password admin[/]
"""

RUN_EPILOG = """[bold]Examples[/]

[dim]All bundled fixtures — counts apply per fixture (3 conclusions x 3 fixtures = 9):[/]

[cyan]bublik-e2e run --runs 9 --day "2026-04-21:ok=1,warning=1,error=1" --publish-dir /srv/logs/e2e --url http://localhost[/]

[dim]Scope to one fixture so the plan matches --runs exactly:[/]

[cyan]bublik-e2e run --runs 10 --day "2026-04-21:basic.ok=7,basic.warning=2,basic.error=1" --publish-dir /srv/logs/e2e --url http://localhost[/]

[dim]DPDK runs with NOK (unexpected) results:[/]

[cyan]bublik-e2e run --runs 5 --day "2026-04-21:dpdk-ethdev-ts.ok=3,dpdk-ethdev-ts.nok-warning=1,dpdk-ethdev-ts.nok-error=1" --publish-dir /srv/logs/e2e --url http://localhost[/]

[dim]net-drv NOK runs with an explicit unexpected percentage mix:[/]

[cyan]bublik-e2e run --runs 4 --mix "warning-mix unexpectedFailed=20%,unexpectedSkipped=5%" --day "2026-04-21:net-drv-ts.ok=2,net-drv-ts.nok-warning@warning-mix=2" --publish-dir /srv/logs/e2e --url http://localhost[/]

[dim]Multi-day campaign across fixtures, creating projects on the way in:[/]

[cyan]bublik-e2e run --runs 6 --setup-projects --day "2026-04-21:basic.ok=2,basic.nok-error=1" --day "2026-04-22:dpdk-ethdev-ts.ok=2,dpdk-ethdev-ts.nok-warning=1" --publish-dir /srv/logs/e2e --url http://localhost[/]

[dim]Fill a whole month with one conclusion:[/]

[cyan]bublik-e2e run --runs 100 --fill ok --dates "2026-04-01..2026-04-30" --setup-projects --publish-dir /srv/logs/e2e --url http://localhost[/]
"""


def _dispatch(func: Callable[[argparse.Namespace], None], **params: object) -> None:
    """Hand a real argparse.Namespace to a core entry point and surface errors."""
    try:
        func(argparse.Namespace(**params))
    except CliError as exc:
        console.print(f"[bold red]error:[/] {exc}")
        raise typer.Exit(code=1)


@app.command(epilog=GENERATE_EPILOG)
def generate(
    runs: RunsOpt,
    fixture: FixtureOpt = None,
    day: DayOpt = None,
    fill: FillOpt = None,
    dates: DatesOpt = None,
    mix: MixOpt = None,
    publish_dir: PublishDirOpt = None,
    pretty: PrettyOpt = False,
    url: UrlOpt = None,
    env_file: EnvFileOpt = None,
    manifest: ManifestOpt = None,
) -> None:
    """Generate bundles into --publish-dir and write the manifest. No import."""
    _dispatch(
        generate_manifest,
        url=url,
        env_file=env_file,
        manifest=manifest,
        fixture=fixture or [],
        runs=runs,
        day=day or [],
        fill=fill,
        dates=dates,
        mix=mix or [],
        publish_dir=publish_dir,
        pretty=pretty,
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
    )


@app.command(epilog=RUN_EPILOG)
def run(
    runs: RunsOpt,
    fixture: FixtureOpt = None,
    day: DayOpt = None,
    fill: FillOpt = None,
    dates: DatesOpt = None,
    mix: MixOpt = None,
    publish_dir: PublishDirOpt = None,
    pretty: PrettyOpt = False,
    url: UrlOpt = None,
    env_file: EnvFileOpt = None,
    manifest: ManifestOpt = None,
    email: EmailOpt = None,
    password: PasswordOpt = None,
    setup_projects: SetupProjectsOpt = False,
    timeout: TimeoutOpt = 600,
) -> None:
    """Generate bundles and import them in one shot (generate + import)."""
    _dispatch(
        generate_and_import,
        url=url,
        env_file=env_file,
        manifest=manifest,
        fixture=fixture or [],
        runs=runs,
        day=day or [],
        fill=fill,
        dates=dates,
        mix=mix or [],
        publish_dir=publish_dir,
        pretty=pretty,
        email=email,
        password=password,
        setup_projects=setup_projects,
        timeout=timeout,
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
