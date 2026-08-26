"""Pydantic model describing a campaign plan file — the schema for plan.yaml.

A plan is a reviewable, commentable serialization of a campaign that would
otherwise be a long command line: it carries exactly the values of ``--runs``,
``--mix`` and ``--day``. This model validates the file's *shape* (and its dates,
mix names and counts); the spec strings it holds are validated where every other
spec string is, by :mod:`core.planning`, so there is one parser per syntax.

``extra="forbid"`` turns a typo like ``dayz:`` into an error instead of a
silently ignored key. Export the JSON Schema with ``bublik-e2e schema --kind
plan`` for editor completion and CI validation.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

# A mix value is an absolute count (3) or a share of the run ("20%").
MixValueSpec = Union[int, float, str]

# One mix, either as a mapping (preferred in YAML — one key per line) or as the
# compact "key=value,key=value" string the --mix option takes.
MixSpec = Union[dict[str, MixValueSpec], str]

# One day, either as a list of "[fixture.]conclusion[@mix][+ui]=count" items
# (preferred — one run group per line) or as a single comma-separated string.
# An empty list is a planned day with no runs.
DaySpec = Union[list[str], str]

MixName = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")]


class PinSpec(BaseModel):
    """A leaf forced to a known status and verdict set in every matching run."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Referenced from a rule's 'pin'.")
    fixture: str = Field(description="Fixture whose tree contains the test.")
    test: str = Field(description="Leaf test name, e.g. 'send_receive'.")
    status: str = Field(
        default="FAILED",
        description="Obtained result status: PASSED, FAILED, SKIPPED, KILLED, ...",
    )
    unexpected: bool = Field(
        default=True,
        description=(
            "Whether the result counts as unexpected. Pinning an unexpected "
            "result into an 'ok' run changes that run's conclusion, so such a "
            "pin should name its conclusions."
        ),
    )
    verdicts: list[str] = Field(
        default_factory=list,
        description=(
            "Verdict strings to author on the leaf. Without these every "
            "unexpected leaf carries the same generated text and a rule "
            "matching on verdicts cannot be shown to discriminate."
        ),
    )
    iterations: list[int] = Field(
        default_factory=list,
        description="Iteration indices (tin) to pin; empty means every one.",
    )
    conclusions: list[str] = Field(
        default_factory=list,
        description="Limit the pin to runs of these conclusions; empty means all.",
    )


class IssueSpecModel(BaseModel):
    """An issue to create before the held-back runs are imported."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Referenced from a rule's 'issue'.")
    title: str
    description: str | None = None
    key: str | None = Field(
        default=None,
        description="External reference, e.g. 'ref://E2E_BUGS/E2E-101'.",
    )
    close: bool = Field(
        default=False,
        description=(
            "Close the issue once its rules exist. Closing deactivates its "
            "rules and lifts suppression — the 'stale classification' state."
        ),
    )


class RuleSpecModel(BaseModel):
    """A rule, created by classifying one result of its pin."""

    model_config = ConfigDict(extra="forbid")

    id: str
    issue: str = Field(description="Id of the issue in 'issues'.")
    pin: str = Field(description="Id of the pin whose result is classified.")
    category: str = Field(default="known-issue")
    expected: bool | None = Field(
        default=None,
        description=(
            "Disposition. true suppresses the failure, false leaves it "
            "counting, null marks it without deciding. Defaults from category."
        ),
    )
    scope: str = Field(
        default="future",
        description="'future' creates an active rule; 'oneoff' stamps only this result.",
    )
    match: list[str] = Field(
        default_factory=list,
        description=(
            "Matcher dimensions to keep beyond the test, which is always "
            "matched: any of parameters, verdicts, tags. Empty is a test-only "
            "rule, matching every iteration of that test."
        ),
    )


class ClassificationSpec(BaseModel):
    """Pinned leaves plus the issues and rules written against them."""

    model_config = ConfigDict(extra="forbid")

    pins: list[PinSpec] = Field(default_factory=list)
    issues: list[IssueSpecModel] = Field(default_factory=list)
    rules: list[RuleSpecModel] = Field(default_factory=list)


class Plan(BaseModel):
    """A versioned fixture campaign."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = Field(description="Plan format version.")
    runs: int | None = Field(
        default=None,
        ge=1,
        description=(
            "Expected total number of runs. Optional; when present it is "
            "asserted against the number the day specs expand to, which catches "
            "an edit that silently adds or drops runs."
        ),
    )
    mixes: dict[MixName, MixSpec] = Field(
        default_factory=dict,
        description=(
            "Named result mixes, referenced from a day item with @name. Values "
            "are absolute counts (3) or shares of the run ('20%')."
        ),
    )
    classification: ClassificationSpec | None = Field(
        default=None,
        description=(
            "Classification fixtures: leaves pinned to known statuses and "
            "verdicts, and the issues and rules written against them. Rules are "
            "created after the API-seeded runs are imported, so a later import "
            "(a '+ui' run) proves they apply to runs that did not exist yet."
        ),
    )
    days: dict[date, DaySpec] = Field(
        description=(
            "Runs per calendar date. Each item is "
            "'[fixture.]conclusion[@mix][+ui]=count'; a trailing +ui marks runs "
            "for import through the Playwright UI form instead of the API."
        ),
    )

    def mix_options(self) -> list[str]:
        """Render the mixes as ``--mix`` option values."""
        return [
            f"{name}:{_render_mix(spec)}" for name, spec in sorted(self.mixes.items())
        ]

    def day_options(self) -> list[str]:
        """Render the days as ``--day`` option values, oldest first."""
        return [
            f"{day.isoformat()}:{_render_day(spec)}"
            for day, spec in sorted(self.days.items())
        ]

    def classification_spec(self) -> dict[str, Any] | None:
        """Render the classification section as the plain mapping core expects.

        ``exclude_unset`` is load-bearing: a rule's ``expected`` is tri-state, so
        an explicit ``expected: null`` ("mark it, decide later") has to stay
        distinguishable from an omitted one ("default from the category"). Every
        other field is defaulted by :mod:`core.classification` when absent.
        """
        if self.classification is None:
            return None
        return self.classification.model_dump(exclude_unset=True)


def _render_mix(spec: MixSpec) -> str:
    if isinstance(spec, str):
        return spec
    return ",".join(f"{key}={value}" for key, value in spec.items())


def _render_day(spec: DaySpec) -> str:
    if isinstance(spec, str):
        return spec
    return ",".join(item.strip() for item in spec if item.strip())


def plan_json_schema() -> dict[str, Any]:
    """JSON Schema for a plan file (editor completion, CI validation)."""
    return Plan.model_json_schema()
