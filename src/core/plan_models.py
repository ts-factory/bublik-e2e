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
