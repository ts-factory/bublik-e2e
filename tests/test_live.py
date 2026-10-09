from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
import subprocess
from typing import Any

import pytest
from typer.testing import CliRunner

from cli import app
from core import importer
from core.bundle import apply_mix, generate_bundle
from core.common import CliError
from core.discovery import discover_fixtures
from core.live import (
    LiveSession,
    build_live_session,
    drop_events,
    live_spec,
    parse_stop_after,
    stream_session,
)
from core.settings import DEFAULT_TIMEZONE

FIXTURES = ("basic", "dpdk-ethdev-ts", "net-drv-ts")
START = datetime(2026, 1, 2, 3, 4, 5, tzinfo=DEFAULT_TIMEZONE)
runner = CliRunner()
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def make_bundle(tmp_path: Path, name: str, conclusion: str = "ok") -> Path:
    fixture = discover_fixtures()[name]
    spec = live_spec(fixture, conclusion, "fixture-default", START)
    bundle = tmp_path / spec.id
    generate_bundle(fixture, spec, bundle, False)
    apply_mix(bundle, [], conclusion, False)
    return bundle


def plan_items(plan: dict[str, Any]) -> list[tuple[str, str]]:
    """Bublik's PlanTracker ids, flattened: the root is 0 and each visit of a
    child (in order, descending first) takes the next id."""
    items = [(plan["name"], plan["type"])]
    for child in plan.get("children", []):
        items.extend(plan_items(child))
    return items


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.t += seconds


class FakePost:
    def __init__(self, run_id: int = 77) -> None:
        self.run_id = run_id
        self.calls: list[tuple[str, Any]] = []

    def __call__(self, url: str, payload: Any) -> Any:
        self.calls.append((url, payload))
        return {"runid": self.run_id} if url.endswith("/init/") else None

    def kinds(self) -> list[str]:
        return [url.split("/importruns/")[1].split("/")[0] for url, _ in self.calls]

    def fed(self) -> list[dict[str, Any]]:
        return [
            event for url, batch in self.calls if "/feed/" in url for event in batch
        ]


@pytest.mark.parametrize("name", FIXTURES)
def test_events_follow_the_plan_bublik_tracks(tmp_path: Path, name: str) -> None:
    session = build_live_session(make_bundle(tmp_path, name))
    plan = plan_items(session.init["plan"])
    stack: list[int] = []
    next_id = 1
    leaves = 0

    for event in (e.payload for e in session.events):
        if event["type"] == "test_start":
            assert event["id"] == next_id
            next_id += 1
            assert event["parent"] == (stack[-1] if stack else 0)
            assert plan[event["plan_id"]] == (event["name"], event["node_type"])
            stack.append(event["id"])
        elif event["type"] == "test_end":
            assert stack.pop() == event["id"]
            if "obtained" in event:
                leaves += 1
        else:
            assert event["test_id"] == stack[-1]

    assert stack == []
    assert next_id - 1 == len(plan)
    assert leaves == session.total_tests
    assert session.finish_ts >= session.events[-1].ts


def test_init_marks_the_run_running(tmp_path: Path) -> None:
    session = build_live_session(make_bundle(tmp_path, "net-drv-ts"))
    metas = {m["name"]: m.get("value") for m in session.init["meta_data"]["metas"]}

    assert metas["RUN_STATUS"] == "RUNNING"
    assert "FINISH_TIMESTAMP" not in metas
    assert datetime.fromisoformat(metas["START_TIMESTAMP"]) == START
    assert session.init["ts"] == START.timestamp()
    assert {"name": "fixture", "value": "net-drv-ts"} in session.init["tags"]


def test_unexpected_leaves_carry_their_expectation(tmp_path: Path) -> None:
    session = build_live_session(make_bundle(tmp_path, "net-drv-ts", "nok-error"))
    ends = [e.payload for e in session.events if e.ends_leaf]
    unexpected = [e for e in ends if "error" in e]

    assert unexpected
    for event in unexpected:
        assert event["expected"][0]["status"] != event["obtained"]["status"] or (
            event["expected"][0]["verdicts"] != event["obtained"]["verdicts"]
        )


def test_artifacts_are_streamed(tmp_path: Path) -> None:
    session = build_live_session(make_bundle(tmp_path, "basic"))

    assert any(e.payload["type"] == "artifact" for e in session.events)


def test_drop_events_loses_whole_detectable_leaves(tmp_path: Path) -> None:
    session = build_live_session(make_bundle(tmp_path, "net-drv-ts"))
    dropped = drop_events(session, 25, seed=3)
    kept = {e.leaf for e in dropped.events}
    lost = {e.leaf for e in session.events if e.leaf is not None} - kept

    assert lost
    assert lost <= set(session.droppable)
    assert len(lost) == round(len(session.droppable) * 0.25)
    assert drop_events(session, 25, seed=3) == dropped
    # Every gap is followed by a test_start whose id skips ahead.
    starts = [
        e.payload["id"] for e in dropped.events if e.payload["type"] == "test_start"
    ]
    assert len(starts) < starts[-1]


def test_drop_rate_is_bounded(tmp_path: Path) -> None:
    session = build_live_session(make_bundle(tmp_path, "basic"))
    with pytest.raises(CliError, match="between 0 and 100"):
        drop_events(session, 120, seed=0)


def test_stream_posts_init_feed_then_finish(tmp_path: Path) -> None:
    session = build_live_session(make_bundle(tmp_path, "basic"))
    clock, post = FakeClock(), FakePost()
    progress: list[int] = []

    result = stream_session(
        "http://bublik",
        session,
        speed=2,
        post=post,
        sleep=clock.sleep,
        clock=clock,
        on_progress=lambda sent, total, label: progress.append(sent),
    )

    kinds = post.kinds()
    assert kinds[0] == "init" and kinds[-1] == "finish"
    assert set(kinds[1:-1]) == {"feed"}
    assert post.calls[1][0] == "http://bublik/api/v2/importruns/feed/?run=77"
    assert post.calls[-1][1] == {"ts": session.finish_ts}
    assert post.fed() == [e.payload for e in session.events]
    assert result.finished and result.run_id == 77
    assert progress == list(range(1, session.total_tests + 1))
    span = session.events[-1].ts - session.init["ts"]
    assert clock.t == pytest.approx(span / 2, abs=1.0)


def test_stream_batches_events_by_interval(tmp_path: Path) -> None:
    session = build_live_session(make_bundle(tmp_path, "basic"))
    fast, slow = FakePost(), FakePost()

    clock = FakeClock()
    stream_session(
        "http://b", session, batch_interval=0, post=fast, sleep=clock.sleep, clock=clock
    )
    clock = FakeClock()
    stream_session(
        "http://b",
        session,
        batch_interval=10,
        post=slow,
        sleep=clock.sleep,
        clock=clock,
    )

    assert slow.kinds().count("feed") < fast.kinds().count("feed")
    assert slow.fed() == fast.fed()


def test_stop_after_leaves_the_run_unfinished(tmp_path: Path) -> None:
    session = build_live_session(make_bundle(tmp_path, "basic"))
    clock, post = FakeClock(), FakePost()

    result = stream_session(
        "http://b", session, stop_after=2, post=post, sleep=clock.sleep, clock=clock
    )

    assert "finish" not in post.kinds()
    assert not result.finished and result.sent_tests == 2
    assert sum(1 for e in post.fed() if "obtained" in e) == 2


def test_stop_after_zero_only_inits(tmp_path: Path) -> None:
    session = build_live_session(make_bundle(tmp_path, "basic"))
    post = FakePost()

    stream_session("http://b", session, stop_after=0, post=post, sleep=lambda s: None)

    assert post.kinds() == ["init"]


def test_init_without_run_id_is_an_error() -> None:
    session = LiveSession(
        init={"ts": 0.0}, events=(), finish_ts=0.0, total_tests=0, droppable=()
    )
    with pytest.raises(CliError, match="did not return a run id"):
        stream_session("http://b", session, post=lambda url, payload: {})


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), ("3", 3), ("0", 0), ("50%", 5), ("25%", 3), ("100%", 10)],
)
def test_parse_stop_after(value: str | None, expected: int | None) -> None:
    assert parse_stop_after(value, 10) == expected


@pytest.mark.parametrize("value", ["-1", "abc", "120%", "5x"])
def test_parse_stop_after_rejects_garbage(value: str) -> None:
    with pytest.raises(CliError, match="--stop-after"):
        parse_stop_after(value, 10)


def test_curl_json_sends_body_on_stdin_and_accepts_no_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, "\n204", "")

    monkeypatch.setattr(importer.subprocess, "run", fake_run)

    assert (
        importer.curl_json("http://b/feed/", method="POST", payload=[{"a": 1}]) is None
    )
    assert captured["input"] == '[{"a": 1}]'
    assert "@-" in captured["command"]
    assert '[{"a": 1}]' not in captured["command"]


def test_cli_dry_run_writes_payloads(tmp_path: Path) -> None:
    out = tmp_path / "live.json"
    result = runner.invoke(
        app, ["live", "basic", "--dry-run", "--output", str(out), "--drop-rate", "50"]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text())
    assert set(payload) == {"init", "feed", "finish"}
    assert payload["feed"][0]["type"] == "test_start"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--then-import", "--stop-after", "2"], "needs a finished run"),
        (["--then-import", "--dry-run"], "cannot be combined"),
        (["--output", "x.json"], "only applies to --dry-run"),
        (["--dry-run", "--speed", "0"], "--speed must be positive"),
        (["--then-import"], "needs the bundle published"),
    ],
)
def test_cli_rejects_conflicting_options(
    args: list[str], message: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("BUBLIK_E2E_PUBLISH_DIR", raising=False)
    result = runner.invoke(app, ["live", "basic", *args])

    assert result.exit_code == 1
    assert message in ANSI_RE.sub("", result.output)


def test_cli_rejects_unknown_fixture() -> None:
    result = runner.invoke(app, ["live", "nope", "--dry-run"])

    assert result.exit_code == 1
    assert "unknown fixture 'nope'" in ANSI_RE.sub("", result.output)
