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
        "schema/e2e-manifest.schema.json is stale; "
        "run `python tools/dump_schema.py`"
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


def test_unknown_key_is_rejected() -> None:
    bad = _minimal_manifest()
    bad["surpriseKey"] = True
    with pytest.raises(Exception):
        Manifest.model_validate(bad)
