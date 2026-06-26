"""Pydantic models describing the e2e manifest — the single source of truth.

The manifest is produced as plain dicts in :mod:`core.manifest`; these models
validate that output (``Manifest.model_validate``) and are the canonical schema
exported to JSON Schema by ``tools/dump_schema.py`` and, from there, to the UI's
Zod validators. Field names are camelCase to match the JSON 1:1, so validation
neither rewrites keys nor changes the serialized manifest.

``extra="forbid"`` makes the models reject unknown keys, so any drift between the
producer and these models surfaces immediately instead of silently flowing to the
UI. Genuinely free-form payloads (tags, raw iteration params/verdicts/artifacts/
measurements, report config ``content``) stay loosely typed on purpose.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Revision(_Model):
    """A single source revision parsed from run metas (``*_GIT_URL`` etc.)."""

    name: str
    url: str | None = None
    branch: str | None = None
    rev: str | None = None


class MeasurementSummary(_Model):
    """One flattened measurement entry across all leaf iterations."""

    testPath: str | None = None
    tool: str | None = None
    metric: str | None = None
    value: float | int | None = None
    units: str | None = None


class PackageSummary(_Model):
    """Per top-level package status rollup."""

    name: str | None = None
    total: int
    byStatus: dict[str, int]


class IterationEntry(_Model):
    """A sampled leaf iteration shown in the UI (see ``sampleTests``).

    ``params``/``verdicts``/``artifacts``/``measurements`` carry the raw,
    provider-shaped payloads and are intentionally left loose.
    """

    name: str | None = None
    tin: int | None = None
    path: list[str] = []
    pathStr: str = ""
    params: dict[str, Any] = {}
    reqs: list[str] = []
    status: str
    expectedStatus: str
    unexpected: bool
    verdicts: list[Any] = []
    artifacts: list[Any] = []
    measurements: list[Any] = []


class ExpectedRun(_Model):
    """The expectations + samples the UI asserts against for one imported run."""

    name: str
    dashboardDate: str
    iterationCount: int
    expectedStatus: str
    expectedStatusByNok: str
    expectedConclusion: str
    expectedConclusionReason: str | None = None
    expectedMatrix: dict[str, int]
    tags: dict[str, Any] = {}
    requirements: list[str] = []
    verdicts: list[str] = []
    measurements: list[MeasurementSummary] = []
    packages: list[PackageSummary] = []
    sampleTests: dict[str, list[IterationEntry]] = {}
    # Resolved during import once the run id is known (core.importer).
    runUrl: str | None = None
    logUrl: str | None = None


class Bundle(_Model):
    """One generated+published fixture run and everything derived from it."""

    id: str
    fixture: str
    conclusionSpec: str
    mix: str
    date: str
    importUrl: str
    project: str
    e2eRunId: str
    runStatus: str | None = None
    startTimestamp: str | None = None
    finishTimestamp: str | None = None
    tags: dict[str, Any] = {}
    revisions: list[Revision] = []
    runUrlTemplate: str
    logUrlTemplate: str
    expectedRuns: list[ExpectedRun]
    # Filled during import (core.importer): the Bublik run id and deep-links.
    runId: int | None = None
    runUrl: str | None = None
    logUrl: str | None = None


class ReportConfig(_Model):
    """A UI report config bundled into the manifest. ``content`` is free-form."""

    project: str
    type: Literal["report"]
    name: str
    description: str = ""
    content: dict[str, Any]


class Manifest(_Model):
    """Top-level e2e manifest written to ``.e2e/e2e-manifest.json``."""

    version: Literal[1]
    generatedAt: str
    baseUrl: str
    uiBaseUrl: str
    dashboardUrl: str
    historyUrl: str
    importUrl: str
    emptyDates: list[str] = []
    configs: list[ReportConfig] = []
    bundles: list[Bundle] = []


def _strip_titles(node: Any) -> None:
    """Drop Pydantic's auto-generated ``title`` keys, recursively, in place."""
    if isinstance(node, dict):
        node.pop("title", None)
        for value in node.values():
            _strip_titles(value)
    elif isinstance(node, list):
        for value in node:
            _strip_titles(value)


def manifest_json_schema() -> dict[str, Any]:
    """JSON Schema for the manifest, ready for downstream codegen.

    Pydantic adds a ``title`` to every field that just echoes the field name; left
    in, those produce a noisy alias type per property in the UI's TypeScript
    codegen. We strip them so the generated types are clean named interfaces. The
    ``$defs`` model names and the model docstrings (``description``) are preserved.
    """
    schema = Manifest.model_json_schema()
    _strip_titles(schema)
    return schema
