# bublik-e2e — the `bublik-e2e` CLI

Deterministic Bublik fixture generation, publication, and API import, packaged
as a single, standalone, installable CLI with all fixture providers bundled.

```text
fixture provider -> generate bundles -> write into --publish-dir (served at {url}/logs/)
                 -> write manifest v1
                 -> import through the API (cookie auth)
```

The tool is **instance-agnostic**: it targets any Bublik instance through
`--url` plus the admin email/password, with no bublik-docker checkout required.
Everything is configured via flags or environment variables.

## Install

A single package bundles the CLI engine and the `basic` / `dpdk` / `net-drv`
providers:

```bash
uv tool install https://github.com/<user>/bublik-e2e     # straight from git
uv tool install .                                        # from a checkout
uv tool install --force .                                # re-install during development
```

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
uv build
uvx twine check dist/*
```

## CI and releases

GitHub Actions runs Ruff, pytest, and package build checks on pull requests and
pushes to `main`. Release tags publish the package and create GitHub Releases.

One-time PyPI setup:

1. Create or claim the `bublik-e2e` project on PyPI.
2. Add a Trusted Publisher for the GitHub repository.
3. Use workflow `.github/workflows/release.yml`.
4. Use environment name `pypi`.

To release:

```bash
# update [project].version in pyproject.toml first
git tag -a v0.1.0 -m "Release v0.1.0"
git push origin v0.1.0
```

The release workflow requires the tag to match `vX.Y.Z` and the tag version to
match `pyproject.toml`. It builds wheel/sdist artifacts, publishes them to PyPI
with Trusted Publishing, then creates a GitHub Release with generated notes and
the built distributions attached.

## Commands

| Command | Does | Talks to the API? |
|---------|------|-------------------|
| `generate` | Generate bundles into `--publish-dir`, write the manifest. **No import.** | No |
| `import` | Read an existing manifest, log in, optionally set up projects, import, show live progress. | Yes |
| `run` | `generate` then `import` in one shot. | Yes |

### Configuration

URL and credentials come from flags, falling back to the existing bublik-docker
`.env` variable names (real env vars, or an explicit `--env-file`):

| Flag | Env fallback | Default |
|------|--------------|---------|
| `--url` | `BUBLIK_FQDN` + `BUBLIK_DOCKER_PROXY_PORT` + `URL_PREFIX` | `http://127.0.0.1:42000` |
| `--email` | `DJANGO_SUPERUSER_EMAIL` | `admin@bublik.com` |
| `--password` | `DJANGO_SUPERUSER_PASSWORD` | `admin` |
| `--publish-dir` | `BUBLIK_E2E_PUBLISH_DIR` | *(required for generate/run)* |
| `--manifest` | — | `./.e2e/e2e-manifest.json` |

`--url` may include a path prefix (e.g. `http://localhost/bublik`); auth, API,
and logs are then served at `{url}/auth`, `{url}/api/v2`, and `{url}/logs`.

### The publish dir ↔ URL mapping

`--publish-dir` is a **full path** to the directory the target instance serves
at `{url}/logs/<name>/`, where `<name>` is the directory's basename. A bundle
`<id>` is written to `<publish-dir>/<id>` and imported from
`{url}/logs/<name>/<id>/`. No layout is assumed — for a docker instance that
serves its logs volume at `/logs`, point `--publish-dir` at
`<data-dir>/logs/logs/e2e`, which is served at `{url}/logs/e2e/`.

## Usage

Generate and publish bundles (omit `--fixture` to auto-discover every bundled
provider; the example uses a docker logs volume as the publish target):

```bash
bublik-e2e generate \
  --url http://localhost:42000 \
  --publish-dir ./data/logs/logs/e2e \
  --runs 6 \
  --mix "warning-mix unexpectedFailed=20%,unexpectedSkipped=5%" \
  --day "2026-04-21:ok=1,warning=1,error=1" \
  --day "2026-04-23:nok-warning@warning-mix=1,nok-error=1,compromised=1"
```

Import an existing manifest through the API:

```bash
bublik-e2e import \
  --url http://localhost:42000 \
  --email admin@bublik.com --password admin
```

The API path logs in (`POST /auth/login/`, cookie session), schedules the
collection at `/api/v2/importruns/source/`, polls `/api/v2/session_import/<job>/`
while showing a live per-run status table, matches every imported source URL,
writes `runId` values, and resolves the per-run deep links into the manifest.

Pass `--setup-projects` to create any missing projects and the per-project
`references` config (with `LOGS_BASES` pointed at `{url}/logs/`) before importing
— omit it to assume the instance is already configured.

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
> suite, which reads the manifest this tool writes.

## Package layout

`src/`:

| Module | Responsibility |
|--------|----------------|
| `cli.py` | CLI entry point, subcommand dispatch |
| `core/settings.py` | flag/env-derived settings and URL helpers |
| `core/discovery.py` | entry-point fixture discovery and `--fixture` loading |
| `core/planning.py` | mix/day/fill parsing and run planning |
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
