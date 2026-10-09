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

from pydantic import BaseModel, ConfigDict, Field


FixtureConclusionSpec = Literal[
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
IterationResultStatus = Literal[
    "PASSED",
    "FAILED",
    "SKIPPED",
    "KILLED",
    "CORED",
    "FAKED",
    "INCOMPLETE",
    "EMPTY",
]
# Mirrors IssueCategory in bublik/data/models/issue.py.
IssueCategory = Literal[
    "product-defect",
    "test-bug",
    "env",
    "known-issue",
    "flaky",
    "to-investigate",
]
# Matcher dimensions a rule keeps beyond the test, which is always matched.
ClassificationMatchDimension = Literal["parameters", "verdicts", "tags"]
RunStatus = Literal[
    "DONE",
    "WARNING",
    "ERROR",
    "RUNNING",
    "BUSY",
    "STOPPED",
    "INTERRUPTED",
]
UIExpectedConclusion = Literal[
    "run-ok",
    "run-warning",
    "run-error",
    "run-running",
    "run-busy",
    "run-stopped",
    "run-interrupted",
    "run-compromised",
]
ExpectedStatusByNok = Literal["success", "warning", "error"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Revision(_Model):
    """A single source revision parsed from run metas (``*_GIT_URL`` etc.)."""

    name: str
    url: str | None = None
    branch: str | None = None
    rev: str | None = None


class MeasurementSummary(_Model):
    """One flattened measurement entry across all leaf iterations.

    The generator always emits every key (values may be null), so all fields are
    required; that keeps the schema honest and the generated TS types free of
    spurious optionality.
    """

    testPath: str | None
    tool: str | None
    metric: str | None
    value: float | int | None
    units: str | None


class PackageSummary(_Model):
    """Per top-level package status rollup."""

    name: str | None
    total: int
    byStatus: dict[str, int]


class ExpectedMatrix(_Model):
    """Expected result counts per (expectation, result-type) cell.

    Keys mirror ``core.constants.MATRIX_KEYS``; the generator always emits every
    cell, so all fields are required.
    """

    expectedPassed: int
    unexpectedPassed: int
    expectedFailed: int
    unexpectedFailed: int
    expectedSkipped: int
    unexpectedSkipped: int
    expectedKilled: int
    unexpectedKilled: int
    expectedCored: int
    unexpectedCored: int
    expectedFaked: int
    unexpectedFaked: int
    expectedIncomplete: int
    unexpectedIncomplete: int
    abnormal: int


class IterationEntry(_Model):
    """A sampled leaf iteration shown in the UI (see ``sampleTests``).

    ``params``/``verdicts``/``artifacts``/``measurements`` carry the raw,
    provider-shaped payloads and are intentionally left loose.
    """

    name: str | None
    tin: int | None
    path: list[str]
    pathStr: str
    params: dict[str, Any]
    reqs: list[str]
    status: IterationResultStatus
    expectedStatus: IterationResultStatus
    unexpected: bool
    verdicts: list[Any]
    artifacts: list[Any]
    measurements: list[Any]


class LogPagesEntry(_Model):
    """A leaf whose published log is worth navigating.

    Either the log is split across several JSON page files, or it is a single
    file long enough that a line near its end is off screen on load. Shorter
    leaves are omitted: every fixture leaf has a log, and listing them all would
    bury the two or three a test can actually use.

    How many pages a log has is a property of how it was *published*, not of its
    row count -- rgt cuts pages on raw-log byte size per node -- so it can only
    be read off the emitted files, which is what the generator does.

    ``tin`` is informational. ``/api/v2/tree/`` returns no path, so the e2e suite
    resolves these entries to tree nodes by ``name``; the fixture therefore gives
    every iteration of a test the same page count, so whichever iteration the
    lookup lands on matches this entry.
    """

    name: str
    path: list[str]
    pathStr: str
    tin: int | None
    #: 1 when the log is published as a single file with no pagination block.
    pagesCount: int
    #: Table rows across every page.
    rowCount: int


class ExpectedRun(_Model):
    """The expectations + samples the UI asserts against for one imported run."""

    name: str
    dashboardDate: str
    iterationCount: int
    expectedStatus: RunStatus
    expectedStatusByNok: ExpectedStatusByNok
    expectedConclusion: UIExpectedConclusion
    expectedConclusionReason: str | None
    expectedMatrix: ExpectedMatrix
    tags: dict[str, Any]
    requirements: list[str]
    verdicts: list[str]
    measurements: list[MeasurementSummary]
    packages: list[PackageSummary]
    logPages: list[LogPagesEntry]
    sampleTests: dict[str, list[IterationEntry]]
    # Resolved during import once the run id is known (core.importer).
    runUrl: str | None = None
    logUrl: str | None = None


class Bundle(_Model):
    """One generated+published fixture run and everything derived from it."""

    id: str
    fixture: str
    conclusionSpec: FixtureConclusionSpec
    mix: str
    date: str
    importUrl: str
    # How the bundle reaches the instance: "api" bundles are imported by the
    # CLI (`bublik-e2e import`); "ui" bundles are left for the Playwright suite
    # to import through the UI, exercising the import form itself.
    importVia: Literal["api", "ui"] = "api"
    project: str
    e2eRunId: str
    runStatus: RunStatus | None
    startTimestamp: str | None
    finishTimestamp: str | None
    tags: dict[str, Any]
    revisions: list[Revision]
    runUrlTemplate: str
    logUrlTemplate: str
    expectedRuns: list[ExpectedRun]
    #: Leaves this bundle had pinned by the plan's classification section.
    pinnedResults: list[PinnedResult] = Field(default_factory=list)
    # Filled during import (core.importer): the Bublik run id and deep-links.
    runId: int | None = None
    runUrl: str | None = None
    logUrl: str | None = None


class ReportConfig(_Model):
    """A UI report config bundled into the manifest. ``content`` is free-form."""

    project: str
    type: Literal["report"]
    name: str
    description: str
    content: dict[str, Any]


class Tracker(_Model):
    """An issue tracker, configured under ISSUES in a project's references config.

    ``id`` is the TRACKER of a ``ref://TRACKER/KEY`` bug key; a key whose
    tracker is not configured in its project has no external link.
    """

    id: str
    name: str
    uri: str


class Project(_Model):
    """A Bublik project the fixtures land in, as --setup-projects configures it."""

    name: str
    fixtures: list[str]
    #: In config order; the first is the UI's default tracker. May be empty.
    trackers: list[Tracker]


class PinnedResult(_Model):
    """One leaf a classification pin forced, as generated into a bundle.

    The fixture tree is identical across every run of a fixture, so a pin
    resolves to the same test and parameters in every run it applies to. That is
    what lets a rule written against one run be asserted against another.
    """

    pin: str
    test: str
    tin: int
    pathStr: str
    params: dict[str, Any]
    status: IterationResultStatus
    unexpected: bool
    verdicts: list[str]


class ClassificationPin(_Model):
    """A pin, with the bundles it landed in split by import wave."""

    id: str
    fixture: str
    test: str
    status: IterationResultStatus
    unexpected: bool
    verdicts: list[str]
    iterations: list[int]
    conclusions: list[str]
    #: Bundle ids imported through the API, before any rule exists.
    seededIn: list[str]
    #: Bundle ids held back for the UI import, which the rules should stamp.
    appliesTo: list[str]


class ClassificationIssue(_Model):
    """An issue the plan declares. ``issueId`` is filled by --setup-classification."""

    id: str
    title: str
    description: str | None
    key: str | None
    close: bool
    issueId: int | None = None
    #: The Bublik project the issue was created in, also filled by
    #: --setup-classification. An issue belongs to exactly one project; which
    #: one follows from the fixture its rules' pins live in.
    projectId: int | None = None
    projectName: str | None = None


class ClassificationRule(_Model):
    """A rule the plan declares. ``ruleId`` is filled by --setup-classification.

    ``match`` lists the matcher dimensions kept beyond the test, which is always
    matched. An empty list is a test-only rule: it matches every iteration of
    that test, in every run of the project.
    """

    id: str
    issue: str
    pin: str
    category: IssueCategory
    #: true suppresses the failure, false leaves it counting, null marks only.
    expected: bool | None
    scope: Literal["future", "oneoff"]
    match: list[ClassificationMatchDimension]
    ruleId: int | None = None
    #: Result ids the rule was created from, once it has been applied.
    classifiedResultIds: list[int] = Field(default_factory=list)


class ClassificationManifest(_Model):
    """Everything the suite needs to drive and assert result classification."""

    pins: list[ClassificationPin]
    issues: list[ClassificationIssue]
    rules: list[ClassificationRule]


class Manifest(_Model):
    """Top-level e2e manifest written to ``.e2e/e2e-manifest.json``."""

    version: Literal[1]
    generatedAt: str
    baseUrl: str
    uiBaseUrl: str
    dashboardUrl: str
    historyUrl: str
    importUrl: str
    emptyDates: list[str]
    configs: list[ReportConfig]
    #: Each project's issue trackers. Absent from manifests that predate it.
    projects: list[Project] = Field(default_factory=list)
    #: Present only when the plan declares a classification section.
    classification: ClassificationManifest | None = None
    bundles: list[Bundle]


def _strip_titles(node: Any, *, is_properties: bool = False) -> None:
    """Drop Pydantic's auto-generated ``title`` keys, recursively, in place.

    A ``properties`` map is keyed by field name, so a ``title`` key there is a
    field called ``title`` (``ClassificationIssue.title``), not metadata — it
    stays, while the schemas under it are stripped like any other.
    """
    if isinstance(node, dict):
        if not is_properties:
            node.pop("title", None)
        for key, value in node.items():
            _strip_titles(value, is_properties=key == "properties")
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
