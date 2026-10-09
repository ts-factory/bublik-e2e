"""Simulate TE live log streaming into Bublik.

A real Test Environment streams a run into Bublik while it executes: its Logger
POSTs ``importruns/init/`` with the run metadata and the execution plan, then
batches of ``test_start`` / ``artifact`` / ``test_end`` events to
``importruns/feed/?run=<id>``, and finally ``importruns/finish/?run=<id>``. This
module replays a generated fixture bundle over that same protocol, paced on the
bundle's own timeline, so the live-import code path and the UI's view of a run
in progress can be exercised without a real TE.

The bundle is produced by the regular ``generate_bundle`` / ``apply_mix``
pipeline, only rebased to start "now". Every event is derived from its
``bublik.json``, so a later source import of the same bundle (``--then-import``)
carries the same key metas (``START_TIMESTAMP`` + ``CFG``) and replaces the live
tree, exactly like TE publishing its logs after streaming.

Protocol constraints mirrored here (``bublik/core/importruns/live``):

* Plan ids are pre-order: the root is 0 and every node visit takes the next id.
* Node ids are sequential from 1 (the root, whose parent is 0). A ``test_start``
  whose id skips ahead tells Bublik the events in between were lost, and it
  fills the gap with LOST results; ``drop_events`` relies on this.
* The live endpoints are open (AllowAny). They must be called *without* the
  admin session cookie: DRF's session authentication enforces CSRF as soon as a
  session is present, and these POSTs carry no token.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import datetime
import json
import math
from pathlib import Path
import random
import shutil
import tempfile
import time as time_module
from typing import Any, Callable
import urllib.parse

from rich.live import Live
from rich.text import Text

from core.bundle import FixtureSpec, apply_mix, generate_bundle, upsert_meta
from core.common import CliError, console, read_json, sanitize_path_part
from core.constants import RUN_COMPLETE_FILE, RUN_STATUS_BY_CONCLUSION
from core.discovery import discover_fixtures, load_fixture
from core.fixture_api import FixtureProvider
from core.importer import curl_json, ensure_api_projects, login, schedule_import
from core.planning import parse_mix_values, validate_conclusion
from core.run_log_schema import (
    load_meta_data_validator,
    load_run_log_validator,
    validate_meta_data,
    validate_run_log,
)
from core.settings import DEFAULT_TIMEZONE, Settings

LIVE_API = "/api/v2/importruns"


@dataclass(frozen=True)
class LiveEvent:
    """One feed event, with the bundle timestamp it is replayed at."""

    payload: dict[str, Any]
    ts: float
    # Index of the leaf test this event belongs to (None for packages), so a
    # leaf's start/artifact/end events can be dropped together.
    leaf: int | None = None
    # True on a leaf's ``test_end``: the unit progress and --stop-after count in.
    ends_leaf: bool = False
    label: str = ""


@dataclass(frozen=True)
class LiveSession:
    init: dict[str, Any]
    events: tuple[LiveEvent, ...]
    finish_ts: float
    total_tests: int
    # Leaves whose loss Bublik can detect: those followed by another
    # ``test_start``. A dropped last child is never noticed (the parent's
    # ``test_end`` just skips over it), so it would vanish instead of being LOST.
    droppable: tuple[int, ...]


@dataclass(frozen=True)
class StreamResult:
    run_id: int
    finished: bool
    sent_tests: int


# --- building the session ----------------------------------------------------


def build_plan(node: dict[str, Any]) -> dict[str, Any]:
    """Execution plan mirroring the ``iters`` tree one node per visit."""
    plan: dict[str, Any] = {"name": node["name"], "type": node["type"]}
    children = node.get("iters") or []
    if children:
        plan["children"] = [build_plan(child) for child in children]
    return plan


def _params(raw: Any) -> list[list[str]]:
    items = raw.items() if isinstance(raw, dict) else raw or []
    return [[str(name), str(value)] for name, value in items]


def _expected(node: dict[str, Any]) -> list[dict[str, Any]]:
    # Keys and notes are left out: the live handler passes them to Bublik as a
    # single string where a list is expected.
    return [
        {
            "status": result.get("status", "PASSED"),
            "verdicts": list(result.get("verdicts") or []),
        }
        for result in (node.get("expected") or {}).get("results") or []
    ]


def _is_json(text: str) -> bool:
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


def build_live_session(bundle_dir: Path, *, interval: int = 1) -> LiveSession:
    meta = read_json(bundle_dir / "meta_data.json")
    bublik = read_json(bundle_dir / "bublik.json")
    roots = bublik.get("iters") or []
    if not roots:
        raise CliError(f"bundle {bundle_dir} has no iterations to stream")
    root = roots[0]

    metas = [dict(item) for item in meta.get("metas", [])]
    upsert_meta(metas, "RUN_STATUS", "RUNNING")
    metas[:] = [item for item in metas if item.get("name") != "FINISH_TIMESTAMP"]
    init = {
        "ts": root["start_ts_utc"],
        "interval": interval,
        "meta_data": {"version": meta.get("version", 1), "metas": metas},
        "tags": [
            {"name": name, "value": value}
            for name, value in sorted((bublik.get("tags") or {}).items())
        ],
        "plan": build_plan(root),
    }

    events: list[LiveEvent] = []
    droppable: list[int] = []
    counters = {"node": 1, "plan": 0, "leaf": 0}

    def visit(node: dict[str, Any], parent_id: int, last_sibling: bool) -> None:
        node_id = counters["node"]
        plan_id = counters["plan"]
        counters["node"] += 1
        counters["plan"] += 1
        children = node.get("iters") or []
        leaf: int | None = None
        if node["type"] == "test" and not children:
            leaf = counters["leaf"]
            counters["leaf"] += 1
            if not last_sibling:
                droppable.append(leaf)
        label = node.get("path_str") or node["name"]

        start: dict[str, Any] = {
            "type": "test_start",
            "id": node_id,
            "parent": parent_id,
            "plan_id": plan_id,
            "ts": node["start_ts_utc"],
            "node_type": node["type"],
            "name": node["name"],
            "params": _params(node.get("params")),
            "hash": node.get("hash", ""),
            "tin": node.get("tin", -1),
        }
        events.append(LiveEvent(start, start["ts"], leaf, label=label))

        result = (node.get("obtained") or {}).get("result") or {}
        for artifact in result.get("artifacts") or []:
            # Bublik parses a JSON-looking artifact body as an MI measurement,
            # and a failure there aborts the whole live run; skip those.
            if not isinstance(artifact, str) or _is_json(artifact):
                continue
            payload = {
                "type": "artifact",
                "test_id": node_id,
                "ts": start["ts"],
                "body": artifact,
            }
            events.append(LiveEvent(payload, start["ts"], leaf, label=label))

        for index, child in enumerate(children):
            visit(child, node_id, index == len(children) - 1)

        end: dict[str, Any] = {
            "type": "test_end",
            "id": node_id,
            "parent": parent_id,
            "plan_id": plan_id,
            "ts": node["end_ts_utc"],
        }
        if node["type"] == "test":
            end["obtained"] = {
                "status": result.get("status", "PASSED"),
                "verdicts": list(result.get("verdicts") or []),
            }
            expected = _expected(node)
            if expected:
                end["expected"] = expected
            if node.get("err"):
                end["error"] = node["err"]
        events.append(
            LiveEvent(end, end["ts"], leaf, ends_leaf=leaf is not None, label=label)
        )

    visit(root, 0, True)
    return LiveSession(
        init=init,
        events=tuple(events),
        finish_ts=root["end_ts_utc"],
        total_tests=counters["leaf"],
        droppable=tuple(droppable),
    )


def drop_events(session: LiveSession, percent: float, seed: int) -> LiveSession:
    """Lose ``percent`` of the droppable leaves, deterministically per ``seed``.

    Node ids are left as assigned, so the next ``test_start`` after a dropped
    leaf skips an id and Bublik records the gap as LOST.
    """
    if not 0 <= percent <= 100:
        raise CliError(f"--drop-rate must be between 0 and 100, got {percent}")
    count = round(len(session.droppable) * percent / 100)
    dropped = set(random.Random(seed).sample(session.droppable, count))
    return replace(
        session,
        events=tuple(event for event in session.events if event.leaf not in dropped),
    )


def to_dry_run(session: LiveSession) -> dict[str, Any]:
    return {
        "init": session.init,
        "feed": [event.payload for event in session.events],
        "finish": {"ts": session.finish_ts},
    }


# --- streaming ----------------------------------------------------------------


def parse_stop_after(value: str | None, total: int) -> int | None:
    """``N`` leaf tests, or ``N%`` of them (rounded up)."""
    if value is None:
        return None
    raw = value.strip()
    try:
        if raw.endswith("%"):
            percent = float(raw[:-1])
            if not 0 <= percent <= 100:
                raise ValueError
            return math.ceil(total * percent / 100)
        count = int(raw)
        if count < 0:
            raise ValueError
        return count
    except ValueError as exc:
        raise CliError(
            f"invalid --stop-after {value!r}; expected a test count or N%"
        ) from exc


def post_live(url: str, payload: Any) -> Any:
    try:
        # No cookie jar on purpose: see the module docstring (CSRF).
        return curl_json(url, method="POST", payload=payload)
    except CliError as exc:
        message = str(exc)
        if "unknown session" in message:
            message += (
                "\nhint: Bublik lost the live-import context. With DEBUG=1 every "
                "cache is a DummyCache, so live import cannot work; run the "
                "instance with DEBUG off."
            )
        elif "processing metadata" in message:
            message += (
                "\nhint: the run's project may not exist on the instance; pass "
                "--setup-projects to create it."
            )
        raise CliError(message) from exc


def stream_session(
    base_url: str,
    session: LiveSession,
    *,
    speed: float = 1.0,
    batch_interval: float = 1.0,
    stop_after: int | None = None,
    post: Callable[[str, Any], Any] = post_live,
    sleep: Callable[[float], None] = time_module.sleep,
    clock: Callable[[], float] = time_module.monotonic,
    on_progress: Callable[[int, int, str], None] | None = None,
) -> StreamResult:
    """Replay ``session`` against ``base_url`` on its own timeline / ``speed``.

    Events are buffered and flushed to ``feed`` at most every
    ``batch_interval`` seconds, the way TE's log listener batches them. With
    ``stop_after`` the stream ends after that many leaf tests and ``finish`` is
    never sent, leaving the run RUNNING like a crashed TE would.
    """
    if speed <= 0:
        raise CliError(f"--speed must be positive, got {speed}")
    if batch_interval < 0:
        raise CliError(f"--batch-interval cannot be negative, got {batch_interval}")

    response = post(f"{base_url}{LIVE_API}/init/", session.init)
    run_id = response.get("runid") if isinstance(response, dict) else None
    if not isinstance(run_id, int):
        raise CliError(f"live init did not return a run id: {response!r}")
    feed_url = f"{base_url}{LIVE_API}/feed/?{urllib.parse.urlencode({'run': run_id})}"
    if stop_after == 0:
        return StreamResult(run_id, finished=False, sent_tests=0)

    origin = session.init["ts"]
    wall_start = clock()
    last_flush = wall_start
    batch: list[dict[str, Any]] = []
    sent = 0

    def flush() -> None:
        nonlocal last_flush
        if batch:
            post(feed_url, list(batch))
            batch.clear()
        last_flush = clock()

    for event in session.events:
        due = wall_start + (event.ts - origin) / speed
        while True:
            now = clock()
            if batch and now - last_flush >= batch_interval:
                flush()
                now = clock()
            if now >= due:
                break
            wake = due if not batch else min(due, last_flush + batch_interval)
            sleep(max(wake - now, 0.0))
        batch.append(event.payload)
        if event.ends_leaf:
            sent += 1
            if on_progress is not None:
                on_progress(sent, session.total_tests, event.label)
            if stop_after is not None and sent >= stop_after:
                flush()
                return StreamResult(run_id, finished=False, sent_tests=sent)
    flush()
    post(
        f"{base_url}{LIVE_API}/finish/?{urllib.parse.urlencode({'run': run_id})}",
        {"ts": session.finish_ts},
    )
    return StreamResult(run_id, finished=True, sent_tests=sent)


def wait_for_import(
    base_url: str,
    job_id: int,
    timeout: int,
    *,
    sleep: Callable[[float], None] = time_module.sleep,
    clock: Callable[[], float] = time_module.monotonic,
) -> int:
    """Poll one source-import job until it succeeds; return its run id."""
    deadline = clock() + timeout
    while clock() < deadline:
        try:
            tasks = curl_json(f"{base_url}/api/v2/session_import/{job_id}/") or []
        except CliError:
            tasks = []
        for task in tasks:
            status = str(task.get("status", "")).upper()
            if status == "FAILURE":
                raise CliError(f"source import failed: {json.dumps(task, indent=2)}")
            run_id = task.get("run_id")
            if status == "SUCCESS" and isinstance(run_id, int) and run_id > 0:
                return run_id
        sleep(2)
    raise CliError(f"source import job {job_id} did not finish within {timeout}s")


# --- orchestration --------------------------------------------------------------


def resolve_fixture(value: str) -> FixtureProvider:
    """A bundled fixture by name, or a provider directory (``fixture.py``)."""
    path = Path(value)
    if path.is_dir():
        return load_fixture(path)
    fixtures = discover_fixtures()
    if value not in fixtures:
        known = ", ".join(sorted(fixtures)) or "none"
        raise CliError(f"unknown fixture {value!r}; bundled fixtures: {known}")
    return fixtures[value]


def live_spec(
    fixture: FixtureProvider, conclusion: str, mix_name: str, now: datetime
) -> FixtureSpec:
    # Fixture profiles may override CFG, so the millisecond START_TIMESTAMP is
    # what keeps the run key metas unique across invocations (Bublik's live init
    # rejects a run that already exists). The id names the bundle directory.
    run_id = f"{sanitize_path_part(fixture.name)}-live-{now:%Y%m%d-%H%M%S}"
    return FixtureSpec(
        id=run_id,
        fixture_name=fixture.name,
        fixture_id=f"{fixture.fixture_id_prefix}:{run_id}",
        project=fixture.project,
        conclusion=conclusion,
        mix_name=mix_name,
        run_date=now.date().isoformat(),
        metas={"RUN_STATUS": RUN_STATUS_BY_CONCLUSION[conclusion]},
        tags={"ordinal": "1"},
        start=now.isoformat(timespec="milliseconds"),
    )


def _validate_options(args: argparse.Namespace, settings: Settings) -> None:
    if args.speed <= 0:
        raise CliError(f"--speed must be positive, got {args.speed}")
    if args.batch_interval < 0:
        raise CliError(f"--batch-interval cannot be negative, got {args.batch_interval}")
    if args.then_import and args.stop_after is not None:
        raise CliError("--then-import needs a finished run; drop --stop-after")
    if args.then_import and args.dry_run:
        raise CliError("--then-import cannot be combined with --dry-run")
    if args.then_import and settings.publish_dir is None:
        raise CliError(
            "--then-import needs the bundle published: pass --publish-dir <path> "
            "(or set BUBLIK_E2E_PUBLISH_DIR)"
        )
    if args.output is not None and not args.dry_run:
        raise CliError("--output only applies to --dry-run")


def _generate(
    args: argparse.Namespace,
    settings: Settings,
    fixture: FixtureProvider,
    spec: FixtureSpec,
    bundle_dir: Path,
) -> None:
    mix = parse_mix_values(args.mix) if args.mix else []
    generate_bundle(fixture, spec, bundle_dir, args.pretty)
    apply_mix(bundle_dir, mix, spec.conclusion, args.pretty)
    # The live stream is what Bublik sees first; the complete-marker is only
    # published once the stream has finished (see --then-import).
    (bundle_dir / RUN_COMPLETE_FILE).unlink(missing_ok=True)
    run_log_schema = settings.run_log_schema
    if run_log_schema is not None:
        validate_run_log(
            bundle_dir / "bublik.json",
            run_log_schema,
            load_run_log_validator(run_log_schema),
        )
    meta_data_schema = settings.meta_data_schema
    if meta_data_schema is not None:
        validate_meta_data(
            bundle_dir / "meta_data.json",
            meta_data_schema,
            load_meta_data_validator(meta_data_schema),
        )


def _setup_project(
    base_url: str, settings: Settings, fixture: FixtureProvider, cookie_jar: Path
) -> None:
    configs = [
        {
            "project": fixture.project,
            "type": "report",
            "name": config["name"],
            "description": config.get("description", ""),
            "content": config["content"],
        }
        for config in getattr(fixture, "report_configs", ())
    ]
    login(base_url, settings, cookie_jar)
    ensure_api_projects(
        {"bundles": [{"project": fixture.project}], "configs": configs},
        base_url,
        cookie_jar,
    )


def simulate_live_run(args: argparse.Namespace) -> None:
    settings = Settings.from_args(args)
    _validate_options(args, settings)
    validate_conclusion(args.conclusion)
    fixture = resolve_fixture(args.fixture)
    now = datetime.now(DEFAULT_TIMEZONE)
    now = now.replace(microsecond=now.microsecond // 1000 * 1000)
    spec = live_spec(
        fixture, args.conclusion, "live-inline" if args.mix else "fixture-default", now
    )

    publish_dir = settings.publish_dir
    work_dir = Path(tempfile.mkdtemp(prefix="bublik-e2e-live-"))
    bundle_dir = (
        publish_dir / spec.id
        if publish_dir is not None and not args.dry_run
        else work_dir / spec.id
    )
    try:
        _generate(args, settings, fixture, spec, bundle_dir)
        session = build_live_session(
            bundle_dir, interval=max(1, math.ceil(args.batch_interval))
        )
        if args.drop_rate:
            session = drop_events(session, args.drop_rate, args.seed)
        stop_after = parse_stop_after(args.stop_after, session.total_tests)

        if args.dry_run:
            rendered = json.dumps(to_dry_run(session), indent=2) + "\n"
            if args.output is None:
                print(rendered, end="")
            else:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(rendered, encoding="utf-8")
                print(str(args.output))
            return

        base_url = settings.base_url
        cookie_jar = work_dir / "cookies.txt"
        if args.setup_projects:
            _setup_project(base_url, settings, fixture, cookie_jar)

        progress = Text()
        with Live(progress, console=console, refresh_per_second=4):

            def on_progress(sent: int, total: int, label: str) -> None:
                progress.plain = f"streaming {spec.id}: {sent}/{total} tests · {label}"

            result = stream_session(
                base_url,
                session,
                speed=args.speed,
                batch_interval=args.batch_interval,
                stop_after=stop_after,
                on_progress=on_progress,
            )
        run_url = settings.run_url_template.format(runId=result.run_id)
        if result.finished:
            console.print(
                f"[green]✓[/] streamed {result.sent_tests}/{session.total_tests} "
                f"tests of {spec.id} live and finished run {result.run_id}"
            )
        else:
            console.print(
                f"[yellow]![/] stopped {spec.id} after {result.sent_tests}/"
                f"{session.total_tests} tests; run {result.run_id} is left RUNNING"
            )

        if args.then_import:
            assert publish_dir is not None
            (bundle_dir / RUN_COMPLETE_FILE).write_text("")
            quoted = "/".join(
                urllib.parse.quote(part) for part in (publish_dir.name, spec.id)
            )
            import_url = f"{settings.logs_base_url}/{quoted}"
            login(base_url, settings, cookie_jar)
            job_id = schedule_import(base_url, import_url, cookie_jar)
            imported = wait_for_import(base_url, job_id, args.timeout)
            if imported == result.run_id:
                console.print(
                    f"[green]✓[/] source import replaced the live data of run "
                    f"{imported}"
                )
            else:
                console.print(
                    f"[yellow]![/] source import created run {imported} instead of "
                    f"updating live run {result.run_id}; check the RUN_KEY_METAS"
                )
                run_url = settings.run_url_template.format(runId=imported)
        print(run_url)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
