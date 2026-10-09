"""Issue trackers per Bublik project, from the plan's per-fixture settings.

A plan declares trackers per fixture (``fixtures.<name>.trackers``); Bublik
configures them per project, under ISSUES in the project's ``references``
config. This module turns the one into the other: the manifest's ``projects``
list, which ``--setup-projects`` writes and the UI suite reads to find, say, the
project with no tracker without hard-coding its name.
"""

from __future__ import annotations

from typing import Any, Mapping

from core.common import CliError
from core.discovery import discover_fixtures

#: What every project got before trackers were configurable, and what a fixture
#: the plan says nothing about still gets.
E2E_BUGS = {
    "id": "E2E_BUGS",
    "name": "E2E Bug Tracker",
    "uri": "https://bugs.example.invalid/issue/",
}
DEFAULT_TRACKERS = (E2E_BUGS,)


def project_trackers(
    fixtures: Mapping[str, Any], spec: Mapping[str, list[dict[str, str]]]
) -> list[dict[str, Any]]:
    """One entry per project the fixtures land in, sorted by project name.

    ``spec`` maps a fixture name to its trackers; a fixture absent from it gets
    :data:`DEFAULT_TRACKERS`. Trackers for a bundled fixture left out of the
    selection (``--fixture``) are ignored; for any other name they are an
    error. Fixtures that share a project must agree, since the project has one
    references config.
    """
    unselected = set(spec) - set(fixtures)
    if unselected:
        known = set(fixtures) | set(discover_fixtures())
        unknown = sorted(unselected - known)
        if unknown:
            raise CliError(
                "trackers declared for unknown fixture(s): "
                + ", ".join(f"fixtures.{name}" for name in unknown)
                + f"; known fixtures: {', '.join(sorted(known))}"
            )

    members: dict[str, list[str]] = {}
    for name, fixture in fixtures.items():
        members.setdefault(fixture.project, []).append(name)

    projects = []
    for project, names in sorted(members.items()):
        names = sorted(names)
        declared = {
            name: [dict(t) for t in spec.get(name, DEFAULT_TRACKERS)] for name in names
        }
        trackers = declared[names[0]]
        if any(other != trackers for other in declared.values()):
            raise CliError(
                f"fixtures {', '.join(names)} share project {project!r} but "
                "declare different trackers; give them the same list"
            )
        projects.append({"name": project, "fixtures": names, "trackers": trackers})
    return projects


def references_issues(trackers: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    """The ISSUES block of a references config. Dict order is tracker order."""
    return {
        tracker["id"]: {"name": tracker["name"], "uri": tracker["uri"]}
        for tracker in trackers
    }
