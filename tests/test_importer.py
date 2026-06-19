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


def _payload(manifest: dict[str, object], revealed: int) -> list[dict[str, object]]:
    """Build a job payload where the first ``revealed`` bundles have a run_id."""
    tasks = []
    for i, bundle in enumerate(manifest["bundles"]):
        done = i < revealed
        tasks.append(
            {
                "status": "SUCCESS" if done else "RECEIVED",
                "run_source_url": bundle["importUrl"],
                "run_id": (i + 1) if done else None,
            }
        )
    return tasks


def test_steady_progress_outlasts_timeout(
    tmp_path: Path, fake_time: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A batch that keeps advancing completes even past the original deadline."""
    manifest = _manifest(4)
    manifest_path = tmp_path / "manifest.json"

    # Reveal one more run_id on each poll; with sleep(2) cadence and a 5s no-progress
    # budget this spans well past a fixed 5s total deadline, yet still finishes.
    revealed = iter([1, 2, 3, 4])

    def fake_curl(url: str) -> list[dict[str, object]]:
        return _payload(manifest, next(revealed))

    monkeypatch.setattr(importer, "curl_json", fake_curl)

    importer.persist_imported_runs(manifest_path, manifest, "http://host", 1, timeout=5)

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

    # Two of three import, then the job goes silent forever.
    def fake_curl(url: str) -> list[dict[str, object]]:
        return _payload(manifest, 2)

    monkeypatch.setattr(importer, "curl_json", fake_curl)

    with pytest.raises(CliError) as excinfo:
        importer.persist_imported_runs(
            manifest_path, manifest, "http://host", 1, timeout=5
        )

    assert "no import progress" in str(excinfo.value)
    assert "run-2" in str(excinfo.value)  # the missing bundle is reported

    saved = read_json(manifest_path)
    assert [b.get("runId") for b in saved["bundles"]] == [1, 2, None]
    assert saved["bundles"][0]["runUrl"] == "http://host/v2/runs/1"
