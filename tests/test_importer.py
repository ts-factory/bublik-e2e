from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core import importer
from core.common import CliError, read_json


class FakeClock:
    """Deterministic stand-in for ``importer.datetime`` driven by fake sleeps."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def now(self) -> SimpleNamespace:
        return SimpleNamespace(timestamp=lambda: self.t)

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def fake_time(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    clock = FakeClock()
    monkeypatch.setattr(importer, "datetime", clock)
    monkeypatch.setattr(
        importer.time_module, "sleep", lambda seconds: clock.advance(seconds)
    )
    return clock


def _manifest(n: int) -> dict[str, object]:
    return {
        "version": 1,
        "bundles": [
            {
                "id": f"run-{i}",
                "importUrl": f"http://host/logs/run-{i}/",
                "runUrlTemplate": "http://host/v2/runs/{runId}",
                "logUrlTemplate": "http://host/v2/log/{runId}",
                "expectedRuns": [{}],
            }
            for i in range(n)
        ],
    }


def _jobs(manifest: dict[str, object]) -> dict[str, int]:
    """One import job per bundle, numbered from 1 (the per-bundle scheme)."""
    return {b["id"]: i + 1 for i, b in enumerate(manifest["bundles"])}


def _job_task(
    manifest: dict[str, object], job_id: int, done: bool
) -> list[dict[str, object]]:
    """Payload of ``session_import/{job_id}/`` for the bundle behind that job."""
    bundle = manifest["bundles"][job_id - 1]
    return [
        {
            "status": "SUCCESS" if done else "RECEIVED",
            "run_source_url": bundle["importUrl"],
            "run_id": job_id if done else None,
        }
    ]


def _job_id(url: str) -> int:
    return int(url.rstrip("/").rsplit("/", 1)[1])


def test_steady_progress_outlasts_timeout(
    tmp_path: Path, fake_time: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A batch that keeps advancing completes even past the original deadline."""
    manifest = _manifest(4)
    manifest_path = tmp_path / "manifest.json"

    # Job N completes on its Nth poll; jobs are polled once per tick, so one more
    # bundle lands each tick. With sleep(2) cadence and a 5s no-progress budget
    # this spans well past a fixed 5s total deadline, yet still finishes.
    polls: dict[int, int] = {}

    def fake_curl(url: str) -> list[dict[str, object]]:
        job_id = _job_id(url)
        polls[job_id] = polls.get(job_id, 0) + 1
        return _job_task(manifest, job_id, done=polls[job_id] >= job_id)

    monkeypatch.setattr(importer, "curl_json", fake_curl)

    importer.persist_imported_runs(
        manifest_path, manifest, "http://host", _jobs(manifest), timeout=5
    )

    saved = read_json(manifest_path)
    assert [b["runId"] for b in saved["bundles"]] == [1, 2, 3, 4]
    # Deep-links resolved from the templates.
    assert saved["bundles"][0]["runUrl"] == "http://host/v2/runs/1"


def test_stall_persists_partial_progress(
    tmp_path: Path, fake_time: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A frozen payload aborts after the no-progress budget but keeps seen run_ids."""
    manifest = _manifest(3)
    manifest_path = tmp_path / "manifest.json"

    # Two of three import, then the last job stays silent forever.
    def fake_curl(url: str) -> list[dict[str, object]]:
        job_id = _job_id(url)
        return _job_task(manifest, job_id, done=job_id <= 2)

    monkeypatch.setattr(importer, "curl_json", fake_curl)

    with pytest.raises(CliError) as excinfo:
        importer.persist_imported_runs(
            manifest_path, manifest, "http://host", _jobs(manifest), timeout=5
        )

    assert "no import progress" in str(excinfo.value)
    assert "run-2" in str(excinfo.value)  # the missing bundle is reported

    saved = read_json(manifest_path)
    assert [b.get("runId") for b in saved["bundles"]] == [1, 2, None]
    assert saved["bundles"][0]["runUrl"] == "http://host/v2/runs/1"


def test_reconcile_refreshes_run_ids_from_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """runIds are prefilled from session_import history and stale ones cleared."""
    manifest = _manifest(3)
    manifest["bundles"][1]["runId"] = 999  # stale: the DB no longer knows it
    manifest["bundles"][2]["runId"] = 777  # stale too

    def fake_curl(url: str, **kwargs: object) -> dict[str, object]:
        # Only run-0 has ever been imported on this instance.
        if "url=" in url and "run-0" in url:
            return {
                "results": [
                    {
                        "status": "SUCCESS",
                        "run_source_url": manifest["bundles"][0]["importUrl"],
                        "run_id": 41,
                    }
                ]
            }
        return {"results": []}

    monkeypatch.setattr(importer, "curl_json", fake_curl)

    importer.reconcile_run_ids(manifest, "http://host")

    assert [b.get("runId") for b in manifest["bundles"]] == [41, None, None]


def test_import_via_api_skips_ui_bundles_and_reused_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only api-bundles without a reconciled runId are scheduled."""
    manifest = _manifest(3)
    manifest["bundles"][1]["importVia"] = "ui"
    manifest_path = tmp_path / "manifest.json"

    monkeypatch.setattr(importer, "login", lambda *a, **k: None)
    # Reconcile finds run-0 already imported; run-1 (ui) and run-2 are not.
    monkeypatch.setattr(
        importer,
        "reconcile_run_ids",
        lambda m, *a, **k: m["bundles"][0].__setitem__("runId", 41),
    )
    scheduled: list[str] = []

    def fake_schedule(base_url: str, import_url: str, cookie_jar: object) -> int:
        scheduled.append(import_url)
        return 7

    monkeypatch.setattr(importer, "schedule_import", fake_schedule)
    monkeypatch.setattr(
        importer,
        "persist_imported_runs",
        lambda path, m, base, jobs, timeout: (
            [b.__setitem__("runId", 42) for b in m["bundles"] if b["id"] in jobs],
            {},
        )[1],
    )

    args = SimpleNamespace(
        url="http://host",
        env_file=None,
        email="a@b.c",
        password="x",
        setup_projects=False,
        timeout=5,
        include_ui=False,
    )
    importer.import_via_api(args, manifest, manifest_path)

    assert scheduled == [manifest["bundles"][2]["importUrl"]]
    assert [b.get("runId") for b in manifest["bundles"]] == [41, None, 42]
