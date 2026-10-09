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
| `live` | Stream one fixture run into the instance over TE's live-import API (`init`/`feed`/`finish`), paced in real time. | Yes |

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

#### Issue trackers per fixture

`--setup-projects` writes each project's issue trackers under `ISSUES` in its
`references` config. By default every project gets one tracker, `E2E_BUGS`. A
plan can declare them per fixture instead:

```yaml
fixtures:
  net-drv-ts:
    trackers:                     # in order; the first is the UI's default
      - {id: NET_BUGS, uri: "https://net-bugs.example.invalid/browse/"}
      - {id: E2E_BUGS, name: E2E Bug Tracker, uri: "https://bugs.example.invalid/issue/"}
  dpdk-ethdev-ts:
    trackers: []                  # no tracker configured at all
  # basic is not listed, so it keeps the default E2E_BUGS
```

`id` is the `TRACKER` of a `ref://TRACKER/KEY` bug key. `name` is the display
name and defaults to the id. `uri` is the prefix the key is appended to. A
fixture left out of `fixtures`, or listed without `trackers`, keeps the default.
Fixtures that share a Bublik project must declare the same list. Unknown fixtures,
duplicate ids and malformed ids or URIs are rejected when the plan is validated,
and the error names the fixture.

Re-running `--setup-projects` converges on the plan: an existing `references`
config is updated in place, not skipped. The manifest records what was asked for
in `projects`, one entry per project with its `fixtures` and `trackers`, so a
scenario can find, say, the project with no tracker without naming it.

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

### Classification fixtures

Bublik applies active issue rules **on import**, so proving a rule works means
showing it stamps a run that did not exist when the rule was written. That needs
three things a plain campaign cannot express, and a `classification:` section
adds all three.

**Pins** force a named leaf to a known status and verdicts. A mix says
"22% unexpectedFailed" and scatters it; a pin says "`rx_mode` fails, with *this*
verdict text". Without authored verdicts every unexpected leaf in every fixture
carries the same generated string, so a rule matching on verdicts cannot be shown
to discriminate.

**Issues and rules** are declared against those pins. A rule keeps only the
matcher dimensions it names in `match`, so one plan can cover a test-only rule, a
verdict rule and a parameters+verdicts rule side by side and compare how they
behave.

```yaml
classification:
  pins:
    - id: rx-mode-timeout
      fixture: net-drv-ts
      test: rx_mode
      status: FAILED
      unexpected: true
      verdicts: ["RX mode negotiation timed out"]
      conclusions: [nok-error]   # keeps the pin out of "ok" runs
    - id: send-receive-flaky
      fixture: net-drv-ts
      test: send_receive
      verdicts: ["Intermittent checksum mismatch"]
      iterations: [0]            # just this iteration; default is all of them

  issues:
    - id: rx-timeout
      title: "RX mode negotiation times out on this NIC"
      key: "ref://E2E_BUGS/E2E-101"
      description: |          # markdown body, optional
        **`rx_mode` never leaves negotiation.** The driver reports the link
        up, but the mode request is never acknowledged.

        - Reproduces on every run since the 6.9 driver bump.
        - Workaround: set the mode with `ethtool -X` first.

  rules:
    - id: rx-by-test          # test-only: every iteration of rx_mode
      issue: rx-timeout
      pin: rx-mode-timeout
      category: known-issue   # expected defaults from the category (true here)
      match: []
    - id: rx-by-verdict       # only results carrying that verdict
      issue: rx-timeout
      pin: rx-mode-timeout
      category: product-defect
      expected: false         # explicit override: classified, still counting
      match: [verdicts]
```

`match` takes any of `parameters`, `verdicts`, `tags`; the test is always
matched. `expected` is tri-state — `true` suppresses the failure, `false` leaves
it counting, `null` marks it without deciding — and defaults from `category`.
Setting `close: true` on an issue closes it once its rules exist, which
deactivates them and lifts suppression: the "stale classification" state.

`description` is free-text markdown, stored on the issue and returned by
`/api/v2/issues/` and `/api/v2/runs/{id}/issues/`. It is optional — the plan
leaves a few issues without one on purpose, so the untriaged, empty-body state
has fixtures too.

Pins are generated into every run they apply to, unconditionally. Creating the
issues and rules through the API takes a flag, which `task e2e:seed` passes by
default:

```bash
bublik-e2e run --plan e2e/plan.yaml --setup-projects --setup-classification
```

Without the flag the pinned results are still generated, and the triage is left
to be done by hand in the UI (or by the Playwright suite) — which is the point of
keeping it a flag: the fixture gives you stable things to classify either way.
From the Taskfile that is `task e2e:seed E2E_CLASSIFY=0`.

The ordering the flag creates is what makes the assertion meaningful:

| Stage | What happens |
|-------|--------------|
| 1 | API-imported bundles are seeded. Their pinned leaves fail with authored verdicts. |
| 2 | `--setup-classification` classifies one result per rule via `POST /results/{id}/classify/`. |
| 3 | The Playwright suite imports the `+ui` bundles — runs generated before any rule existed. |
| 4 | Bublik stamps them on import; the suite asserts the stamps. |

The manifest records both halves. Each pin lists `seededIn` (the runs a rule can
be written against) and `appliesTo` (the runs it should reach), each bundle
carries its `pinnedResults`, and `issueId`/`ruleId` are filled in once
`--setup-classification` has run — which is also how the suite tells whether to
create the issues itself. Each issue also records the `projectId`/`projectName`
it landed in: an issue belongs to exactly one project, and which one follows
from the fixture its rules' pins live in, so a plan that points one issue's
rules at two fixtures is rejected when the plan is validated.

> **Adding or changing a pin needs a re-import.** Regenerating a bundle does not
> re-import it: reconciliation matches on the source URL and finds the run
> already in the instance, so the *old* results stay, without the new authored
> verdicts. `--setup-classification` refuses to classify one of those rather
> than quietly building a broader rule than the plan asks for — a rule matching
> on verdicts, handed a result with none, captures an empty list, and an empty
> dimension is not applied, so it silently becomes test-only. Reset the stack
> (`down --volumes`, then up and seed) after editing pins.

> The same goes for an issue's `title`, `key` or `description`: they are sent
> on the request that *creates* the issue, and every later rule references the
> id the backend handed back, so an edit reaches an already-seeded instance
> only after the same reset.

Rules may also be written inline under their issue, which supplies the `issue`
link and derives an omitted `id` from the issue's — worth it once a plan carries
dozens of issues:

```yaml
  issues:
    - id: rx-timeout
      title: "RX mode negotiation times out on this NIC"
      key: ref://E2E_BUGS/E2E-101
      rules:
        - {pin: rx-mode-timeout, category: known-issue, match: []}
        - {pin: rx-mode-timeout, category: product-defect, expected: false, match: [verdicts]}
```

Two more shapes cover states that classifying a result never produces on its
own: an issue nobody has classified anything into yet, and a rule that exists
on an open issue but has been switched off.

```yaml
  issues:
    - id: untriaged-crash          # no rules: created directly, so name its fixture
      title: "Driver crash on unload, not yet triaged"
      key: ref://E2E_BUGS/E2E-140
      fixture: net-drv-ts
    - id: rx-timeout
      title: "RX mode negotiation times out on this NIC"
      rules:
        - {pin: rx-mode-timeout, match: []}
        - {pin: rx-mode-timeout, match: [verdicts], active: false}   # paused
```

`fixture` names the project an issue belongs to. It is required on an issue
without rules, which `--setup-classification` creates through
`POST /api/v2/issues/` in the project the fixture's bundles import into. On an
issue with rules it is optional and, when given, must match the fixture of the
rules' pins. The manifest records the resolved `fixture` on every issue.

`active` defaults to `true`. A rule with `active: false` is created by
classifying its pin like any other, then switched off through
`POST /api/v2/issue_rules/deactivate/`, so it stays on an open issue and stamps
nothing new. It is rejected on a `oneoff` rule, which is created inactive, and
under an issue with `close: true`, whose close deactivates every rule anyway.
The manifest records `active` on every rule.

`--setup-classification` works in this order: rule-less issues, then rules,
then deactivation, then closing. Re-running it creates nothing; deactivation and
closing are collection actions, so a re-run sends the same ids and the backend
reports them unchanged.

## Live import simulation

A real Test Environment streams a run into Bublik while it executes: `POST
/api/v2/importruns/init/` with the metadata and execution plan, batches of
`test_start` / `artifact` / `test_end` events to `feed/?run=<id>`, then
`finish/?run=<id>`. `bublik-e2e live` replays one fixture run over that same
protocol, so the live-import code and the UI's view of a run in progress can be
exercised without a TE:

```bash
# Watch a run appear as RUNNING and fill in, 5x faster than the fixture timeline
bublik-e2e live net-drv-ts --speed 5 --setup-projects --url http://localhost:42000

# Crashed TE: stop after 30% of the tests, never finish (the run stays RUNNING)
bublik-e2e live net-drv-ts --speed 10 --stop-after 30%

# Lost events: drop 10% of the tests; Bublik fills the gaps with LOST results
bublik-e2e live dpdk-ethdev-ts --speed 50 --drop-rate 10 --seed 1

# The real TE flow: stream live, then publish the bundle and source-import it,
# which replaces the live tree of the same run (matched on START_TIMESTAMP + CFG)
bublik-e2e live basic --then-import --publish-dir ./data/logs/logs/e2e

# Inspect the init/feed/finish payloads without contacting Bublik
bublik-e2e live basic --dry-run --output live.json
```

- The run is generated by the usual pipeline (`--conclusion`, inline `--mix`)
  and rebased to start now. Event timestamps are the bundle's own, so with
  `--speed` above 1 they run ahead of the wall clock.
- At `--speed 1` a run replays in real time (the synthetic fixtures take about
  3 s per test: net-drv-ts is ~26 min, dpdk-ethdev-ts ~4.5 h).
- `--then-import` needs `--publish-dir`. The bundle is written there as
  `<fixture>-live-<timestamp>/`, and the `.done` marker is only added once the
  stream finishes. The next `generate` replaces the publish dir and removes it.
- Bublik must run with `DEBUG` off: in debug mode every cache is a
  `DummyCache`, the live-import context is lost after `init`, and every `feed`
  fails with "unknown session".
- Measurements are not streamed (TE sends them as MI artifacts); they show up
  after `--then-import`.

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
| `core/classification.py` | pins, issues and rules: the plan's classification section |
| `core/classify_api.py` | creating those issues and rules against imported runs |
| `core/importer.py` | API import path and live progress table |
| `core/live.py` | TE live-import simulation (`live` command) |
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
