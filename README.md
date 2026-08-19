# bublik-e2e — the `bublik-e2e` CLI

Deterministic Bublik fixture generation, publication, and API import, packaged
as a single installable CLI with all fixture providers bundled.

```text
fixture provider -> generate bundles -> validate bublik.json and meta_data.json
                                    against explicit Draft 7 schemas
                 -> write into --publish-dir (served at {url}/logs/)
                 -> write manifest v1
                 -> import through the API (cookie auth)
```

The tool is **instance-agnostic**: it targets any Bublik instance through
`--url` plus the admin email/password. Everything is configured via flags or
environment variables.

## Install

A single package bundles the CLI engine and the `basic` / `dpdk-ethdev-ts` /
`net-drv-ts` providers (these names are what you reference in `--day` specs).
Install the current GitHub version with:

```bash
uv tool install git+https://github.com/okt-limonikas/bublik-e2e.git
```

For local development:

```bash
uv tool install .            # from a checkout
uv tool install --force .    # re-install after local changes
```

Re-run `uv tool install --force .` (or `uv sync`) after changing bundled
providers' entry points so renamed registrations take effect.

This puts a `bublik-e2e` executable on your PATH. From a workspace checkout you
can also run it without installing via `uv run bublik-e2e <command>`.

## Develop

```bash
uv sync                      # creates .venv with the package installed editable
uv run bublik-e2e --help
```

Local checks mirror CI:

```bash
uv sync --frozen --group dev
uv run ruff check src tests
uv run ruff format --check tests
uv run pytest
```

## Commands

| Command | Does | Talks to the API? |
|---------|------|-------------------|
| `generate` | Generate bundles into `--publish-dir`, write the manifest. **No import.** | No |
| `import` | Read an existing manifest, log in, optionally set up projects, import, show live progress. | Yes |
| `run` | `generate` then `import` in one shot. | Yes |
| `plan` | Expand a plan file and print what it would generate. **Writes nothing.** | No |

### Configuration

URL and credentials come from flags, falling back to environment variables
(real env vars, or an explicit `--env-file`):

| Flag | Env fallback | Default |
|------|--------------|---------|
| `--url` | `BUBLIK_FQDN` + `BUBLIK_DOCKER_PROXY_PORT` + `URL_PREFIX` | `http://127.0.0.1:42000` |
| `--email` | `DJANGO_SUPERUSER_EMAIL` | `admin@bublik.com` |
| `--password` | `DJANGO_SUPERUSER_PASSWORD` | `admin` |
| `--publish-dir` | `BUBLIK_E2E_PUBLISH_DIR` | *(required for generate/run)* |
| — | `BUBLIK_DJANGO_ROOT` | Bublik repository root containing `manage.py` |
| `--run-log-schema` | `BUBLIK_E2E_RUN_LOG_SCHEMA` | `$BUBLIK_DJANGO_ROOT/bublik/data/schemas/run_log.json` |
| `--meta-data-schema` | `BUBLIK_E2E_META_DATA_SCHEMA` | `$BUBLIK_DJANGO_ROOT/bublik/data/schemas/meta_data.json` |
| `--manifest` | — | `./.e2e/e2e-manifest.json` |

Set `BUBLIK_DJANGO_ROOT` to the root of a separate Bublik Django checkout (the
directory containing `manage.py`):

```bash
export BUBLIK_DJANGO_ROOT=/path/to/bublik
```

The run-log and metadata schemas are deliberately not bundled, so generation
validates fixtures against the checked-out Bublik version. Direct schema flags
or `BUBLIK_E2E_RUN_LOG_SCHEMA` / `BUBLIK_E2E_META_DATA_SCHEMA` override paths
derived from `BUBLIK_DJANGO_ROOT`.

`--url` may include a path prefix (e.g. `http://localhost/bublik`); auth, API,
and logs are then served at `{url}/auth`, `{url}/api/v2`, and `{url}/logs`.

### The publish dir ↔ URL mapping

`--publish-dir` is a **full path** to the directory the target instance serves
at `{url}/logs/<name>/`, where `<name>` is the directory's basename. A bundle
`<id>` is written to `<publish-dir>/<id>` and imported from
`{url}/logs/<name>/<id>/`. No layout is assumed — for an instance that serves
its logs volume at `/logs`, point `--publish-dir` at `<data-dir>/logs/logs/e2e`,
which is served at `{url}/logs/e2e/`.

## Usage

Generate and publish bundles (omit `--fixture` to auto-discover every bundled
provider). The run count is derived from the `--day` specs, so `--runs` is not
needed here. `generate` and `run` require Bublik's Draft 7 run-log and metadata
schemas. Normally, set `BUBLIK_DJANGO_ROOT`; it and the direct schema variables
may also be supplied in the file passed with `--env-file`. The CLI checks that
both schemas are readable and valid Draft 7 before clearing `--publish-dir`,
then validates each finalized `bublik.json` and `meta_data.json` after result
mixes are applied and before deriving the manifest.

Validation failures identify the bundle and schema paths and list deterministic
JSON-pointer errors, capped at the first 20 errors:

```bash
bublik-e2e generate \
  --url http://localhost:42000 \
  --publish-dir ./data/logs/logs/e2e \
  --mix "warning-mix:unexpectedFailed=20%,unexpectedSkipped=5%" \
  --day "2026-04-21:basic.ok=1,basic.warning=1,basic.error=1" \
  --day "2026-04-23:dpdk-ethdev-ts.nok-warning@warning-mix=1,dpdk-ethdev-ts.nok-error=1,dpdk-ethdev-ts.compromised=1"
```

A named `--mix` is only worth defining when reused across specs. For a one-off,
inline the mix directly on the `--day` spec (`;`-separated, no pre-definition):

```bash
bublik-e2e generate \
  --publish-dir ./data/logs/logs/e2e \
  --day "2026-04-21:net-drv-ts.nok-warning@unexpectedFailed=20%;unexpectedSkipped=5%=2"
```

An unprefixed conclusion (e.g. `ok=1`) applies to **every** discovered fixture;
prefix it with a fixture name (`basic.ok=1`) to scope it. Pass `--runs` only in
`--fill` mode, or with `--day` as an optional assertion that the derived count
matches.

### Plan files

A campaign that outgrows one command line belongs in a plan file — a versioned,
reviewable, commentable YAML rendering of the same `--runs`/`--mix`/`--day`
values:

```yaml
version: 1
runs: 3

mixes:
  # Values are shares of the run ("20%") or absolute counts (1).
  warn:
    unexpectedFailed: 20%
    expectedKilled: 1

days:
  2026-04-19: []          # a planned empty day
  2026-04-20:
    - basic.ok=1
    - net-drv-ts.nok-warning@warn=1
    - basic.ok+ui=1       # imported through the UI, not the API
```

```bash
bublik-e2e plan --plan e2e/plan.yaml            # validate and summarize
bublik-e2e run  --plan e2e/plan.yaml --setup-projects
```

`--plan` is mutually exclusive with `--day`/`--fill`; an explicit `--runs` or an
extra `--mix` on the command line still wins, so a plan can be tweaked without
editing the file. Days and mixes each accept the compact string form too
(`2026-04-20: "basic.ok=1,basic.warning=1"`), and because JSON is valid YAML a
`.json` plan loads unchanged.

`bublik-e2e plan` expands the campaign and prints what it would generate —
grouped by `--by date` (default), `fixture` or `conclusion` — without writing
anything:

```
39 runs, 5 dates with runs, 1 empty, 2 imported through the UI
```

The file's shape is validated against a JSON Schema generated from
`core/plan_models.py`; export it for editor completion or CI with
`bublik-e2e schema --kind plan`. Everything inside a spec string (mix keys,
conclusions, fixture names, counts) is validated by the same parser the
command-line options use, so the two paths cannot drift.

Import an existing manifest through the API:

```bash
bublik-e2e import \
  --url http://localhost:42000 \
  --email admin@bublik.com --password admin
```

The API path logs in (`POST /auth/login/`, cookie session), reconciles the
manifest against the instance's import history (`/api/v2/session_import/?url=`;
already-imported bundles are skipped, stale `runId`s cleared), schedules one job
per remaining bundle at `/api/v2/importruns/source/`, polls
`/api/v2/session_import/<job>/` while showing a live per-run status table, writes
`runId` values, and resolves the per-run deep links into the manifest. Because
of the reconcile step, re-running `import` (or importing into an
already-populated instance) is idempotent.

Pass `--setup-projects` to create any missing projects and the per-project
`references` config (with `LOGS_BASES` pointed at `{url}/logs/`) before importing
— omit it to assume the instance is already configured.

Runs planned with a `+ui` marker (e.g. `--day "2026-04-21:basic.ok+ui=1"`) get
`importVia: "ui"` in the manifest and are **not** imported by the CLI — the
Playwright suite imports them through the UI import form, which keeps that form
itself under test. Pass `--include-ui` to pull them through the API anyway.

Generate and immediately import:

```bash
bublik-e2e run \
  --url http://localhost:42000 \
  --email admin@bublik.com --password admin \
  --setup-projects \
  --publish-dir ./data/logs/logs/e2e \
  --runs 100 --fill ok --dates "2026-04-01..2026-04-30"
```

> UI import is **not** part of the CLI — it is handled by the Bublik Playwright
> suite, which reads the manifest this tool writes and imports the bundles
> marked `importVia: "ui"`.

Print the manifest JSON Schema (consumed by the UI repo's type codegen):

```bash
bublik-e2e schema                 # to stdout
bublik-e2e schema --out schema.json
```

## Package layout

`src/`:

| Module | Responsibility |
|--------|----------------|
| `cli.py` | CLI entry point, subcommand dispatch |
| `core/settings.py` | flag/env-derived settings and URL helpers |
| `core/discovery.py` | entry-point fixture discovery and `--fixture` loading |
| `core/planning.py` | mix/day/fill parsing and run planning |
| `core/plan_file.py` / `core/plan_models.py` | plan-file loading and its schema |
| `core/bundle.py` | bundle generation, metadata, and result mixes |
| `core/manifest.py` | manifest assembly and expectation extraction |
| `core/importer.py` | API import path and live progress table |
| `core/fixture_api.py` / `core/synthetic_fixture.py` | the public fixture-authoring API |
| `fixtures/` | bundled `basic` / `dpdk` / `net_drv` providers |

## Fixture providers

Providers are discovered two ways:

- **Entry points (default).** When `--fixture` is omitted, the CLI discovers
  every provider registered under the `bublik_e2e.fixtures` entry-point group.
  Any installed fixture package registers automatically — declare it in your
  `pyproject.toml`:

  ```toml
  [project.entry-points."bublik_e2e.fixtures"]
  my-fixture = "my_package.my_fixture:fixture"
  ```

- **`--fixture <dir>`.** A directory containing `fixture.py` that exports a
  `fixture` object; may be repeated. Useful for ad-hoc providers.

Each provider exports a `fixture` object. Subclass `BaseFixture` (re-exported
from `core`) to inherit the `bublik-e2e` project, `e2e` prefix, and
`fixture-default` mix, overriding only what differs:

```python
from core import BaseFixture


class Fixture(BaseFixture):
    name = "example"

    def generate(self, output_dir: Path, pretty: bool) -> None:
        # Write output_dir/meta_data.json and output_dir/bublik.json.
        ...


fixture = Fixture()
```

The bundled providers live in `src/fixtures/` (`basic/`, `dpdk/`, `net_drv/`).
The `basic` provider is self-contained (its converter and raw log are bundled
under `basic/assets/`). The DPDK and net-driver providers generate their bundles
from code; their `raw-log-example/` directories are local reference assets.

## Manifest version 1

The generated manifest carries enough run detail to drive declarative UI
assertions — navigation URLs, tags, revisions, requirements, verdicts,
measurements, and per-package counts. The collection `importUrl` schedules every
generated run in one import job; per-bundle URLs map job tasks back to manifest
entries. `run{Url,UrlTemplate}` / `log{Url,UrlTemplate}` are written at generate
time as `{runId}` templates and resolved to concrete URLs by the API import.

The Bublik Playwright suite reads this manifest (default
`./.e2e/e2e-manifest.json`, override with `--manifest`). When importing against a
different host than the one used at generate time, pass `--url`; the importer
rewrites the stored base URL in the manifest so the server fetches logs from the
right host.
