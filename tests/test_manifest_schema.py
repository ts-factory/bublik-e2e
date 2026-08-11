from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.manifest_models import Manifest, manifest_json_schema

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schema" / "e2e-manifest.schema.json"


def test_committed_schema_matches_models() -> None:
    """The committed JSON Schema must match the Pydantic models.

    Regenerate with ``python tools/dump_schema.py`` after changing the models.
    """
    expected = json.dumps(manifest_json_schema(), indent=2, sort_keys=True) + "\n"
    assert SCHEMA_PATH.read_text(encoding="utf-8") == expected, (
        "schema/e2e-manifest.schema.json is stale; run `python tools/dump_schema.py`"
    )


def _minimal_manifest() -> dict:
    return {
        "version": 1,
        "generatedAt": "2026-04-21T00:00:00+00:00",
        "baseUrl": "http://host",
        "uiBaseUrl": "http://host",
        "dashboardUrl": "http://host/dashboard",
        "historyUrl": "http://host/history",
        "importUrl": "http://host/logs/seg/",
        "emptyDates": [],
        "configs": [],
        "bundles": [],
    }


def test_minimal_manifest_validates() -> None:
    Manifest.model_validate(_minimal_manifest())


def test_stable_manifest_domains_are_finite() -> None:
    definitions = manifest_json_schema()["$defs"]
    bundle = definitions["Bundle"]["properties"]
    expected_run = definitions["ExpectedRun"]["properties"]
    iteration = definitions["IterationEntry"]["properties"]

    conclusion_specs = [
        "ok",
        "nok-warning",
        "nok-error",
        "warning",
        "error",
        "running",
        "busy",
        "stopped",
        "interrupted",
        "compromised",
    ]
    iteration_statuses = [
        "PASSED",
        "FAILED",
        "SKIPPED",
        "KILLED",
        "CORED",
        "FAKED",
        "INCOMPLETE",
        "EMPTY",
    ]
    run_statuses = [
        "DONE",
        "WARNING",
        "ERROR",
        "RUNNING",
        "BUSY",
        "STOPPED",
        "INTERRUPTED",
    ]
    expected_conclusions = [
        "run-ok",
        "run-warning",
        "run-error",
        "run-running",
        "run-busy",
        "run-stopped",
        "run-interrupted",
        "run-compromised",
    ]

    assert bundle["conclusionSpec"]["enum"] == conclusion_specs
    assert bundle["runStatus"]["anyOf"][0]["enum"] == run_statuses
    assert expected_run["expectedStatus"]["enum"] == run_statuses
    assert expected_run["expectedStatusByNok"]["enum"] == [
        "success",
        "warning",
        "error",
    ]
    assert expected_run["expectedConclusion"]["enum"] == expected_conclusions
    assert iteration["status"]["enum"] == iteration_statuses
    assert iteration["expectedStatus"]["enum"] == iteration_statuses


def test_unknown_key_is_rejected() -> None:
    bad = _minimal_manifest()
    bad["surpriseKey"] = True
    with pytest.raises(Exception):
        Manifest.model_validate(bad)
