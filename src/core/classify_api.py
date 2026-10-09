"""Create the plan's classification issues and rules against imported runs.

This runs after the API import wave and before the held-back ``+ui`` bundles are
imported by the Playwright suite. That ordering is the whole point: a rule
created here did not exist when the ``+ui`` run was generated, so when Bublik
stamps that run on import it is demonstrably the *rule* doing the work and not
the fixture.

Rules are created through ``POST /results/{id}/classify/`` — the endpoint the UI
itself calls — rather than by POSTing to ``/issue_rules/`` directly. That way the
fixture exercises the real triage path, and the backend resolves the test and
captures the matcher from the result, so this module never has to look up a test
id or reconstruct a parameter dict.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import urllib.parse

from core.classification import MATCH_DIMENSIONS
from core.common import CliError, console, write_json

# An omitted matcher key means "capture it from the result"; an explicitly empty
# one means "do not apply this criterion". So a rule keeps exactly the dimensions
# it names and suppresses the rest, without this module knowing their values.
EMPTY_BY_DIMENSION: dict[str, Any] = {"parameters": {}, "verdicts": [], "tags": []}


def matcher_for(match: list[str]) -> dict[str, Any]:
    """Build the classify matcher that keeps only ``match``.

    >>> matcher_for([])
    {'parameters': {}, 'verdicts': [], 'tags': []}
    >>> matcher_for(["verdicts"])
    {'parameters': {}, 'tags': []}
    """
    return {
        dimension: EMPTY_BY_DIMENSION[dimension]
        for dimension in MATCH_DIMENSIONS
        if dimension not in match
    }


def _param_strings(params: dict[str, Any]) -> set[str]:
    """Render a pinned leaf's params the way the API renders a result's."""
    return {f"{key}={value}" for key, value in params.items() if key and value}


def find_result(
    base_url: str,
    run_id: int,
    record: dict[str, Any],
    cookie_jar: Path,
    fetch: Any,
    cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve one pinned leaf to its result row in an imported run.

    The whole row is returned, not just ``result_id``: it also carries
    ``project_id``/``project_name``, which the classify payload needs and which
    would otherwise take a second request to recover.

    Filtered by test name only, then narrowed to ``run_id`` here. The endpoint's
    ``parent_id`` looks like the obvious filter but is not: it matches
    ``parent_package``, the leaf's *immediate* parent, so passing a run id
    returns only results sitting directly under the run root and silently misses
    every test nested in a package. The UI passes a tree node id there, which
    would mean walking ``/tree/`` first -- and that endpoint rejects a run id
    outright, resolving results with ``test_run_id__isnull=False`` while a run
    root has none.

    Parameters are compared as a subset, mirroring the backend's own matcher, so
    a result carrying extra arguments still matches.
    """
    query = urllib.parse.urlencode({"test_name": record["test"]})
    url = f"{base_url}/api/v2/results/?{query}"
    if cache is None:
        payload = fetch(url, cookie_jar=cookie_jar)
    elif url in cache:
        payload = cache[url]
    else:
        payload = fetch(url, cookie_jar=cookie_jar)
        cache[url] = payload
    results = payload.get("results", []) if isinstance(payload, dict) else []
    in_run = [
        result for result in results if int(result.get("run_id", 0)) == int(run_id)
    ]
    if not in_run:
        raise CliError(
            f"pin {record['pin']!r}: run {run_id} has no result for test "
            f"{record['test']!r}"
        )
    wanted = _param_strings(record.get("params") or {})
    for result in in_run:
        got = set(result.get("parameters") or [])
        if not wanted <= got:
            continue
        # The pin's verdicts must actually be on the imported result. When they
        # are not, the run in the instance predates the pin: regenerating a
        # bundle does not re-import it, since reconciliation matches on the
        # source URL and finds the old run. Classifying anyway would silently
        # create a *broader* rule than the plan declares -- a rule that asked to
        # match on verdicts, given none to match on, becomes test-only and
        # stamps every iteration of that test.
        expected_verdicts = set(record.get("verdicts") or [])
        if expected_verdicts:
            live = set(result["obtained_result"].get("verdicts") or [])
            if not expected_verdicts <= live:
                raise CliError(
                    f"pin {record['pin']!r}: result {result['result_id']} in run "
                    f"{run_id} does not carry the pin's verdicts "
                    f"{sorted(expected_verdicts)} (it has {sorted(live)}). "
                    "The imported run is older than the pin -- re-import it, or "
                    "reset the stack, before applying classification."
                )
        return result
    raise CliError(
        f"pin {record['pin']!r}: no result in run {run_id} matches test "
        f"{record['test']!r} with parameters {sorted(wanted)}; the run has "
        f"{len(in_run)} result(s) for that test"
    )


def _seeded_bundle(
    manifest: dict[str, Any], pin_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The first API-imported bundle carrying ``pin_id``, and that pin's record."""
    for bundle in manifest.get("bundles", []):
        if bundle.get("importVia", "api") != "api" or not bundle.get("runId"):
            continue
        for record in bundle.get("pinnedResults") or []:
            if record["pin"] == pin_id:
                return bundle, record
    raise CliError(
        f"pin {pin_id!r} landed in no imported run: a rule can only be written "
        "against a run that is already in the instance"
    )


def _project_of(
    manifest: dict[str, Any],
    issue: dict[str, Any],
    base_url: str,
    cookie_jar: Path,
    fetch: Any,
    projects: dict[str, int],
) -> int:
    """The id of the project an issue's fixture imports into.

    A rule-less issue has no result to borrow a project from, so the name comes
    from a bundle of its fixture and the id from ``/projects/``, fetched once.
    """
    if not issue.get("fixture"):
        # Only a manifest written before issues carried a fixture lacks one.
        raise CliError(
            f"issue {issue['id']!r} has no rules and no fixture, so nothing names "
            "its project; regenerate the manifest from a plan that gives it one"
        )
    name = next(
        (
            bundle["project"]
            for bundle in manifest.get("bundles", [])
            if bundle.get("fixture") == issue["fixture"]
        ),
        None,
    )
    if name is None:
        raise CliError(
            f"issue {issue['id']!r} names fixture {issue['fixture']!r}, which no "
            "bundle in the manifest imports, so it has no project to belong to"
        )
    if not projects:
        listed = fetch(f"{base_url}/api/v2/projects/", cookie_jar=cookie_jar)
        projects.update({project["name"]: int(project["id"]) for project in listed})
    if name not in projects:
        raise CliError(
            f"issue {issue['id']!r}: project {name!r} does not exist in the "
            "instance; import its fixture's runs first"
        )
    return projects[name]


def apply_classification(
    manifest: dict[str, Any],
    manifest_path: Path,
    base_url: str,
    cookie_jar: Path,
    fetch: Any,
) -> None:
    """Create the plan's issues and rules, recording their ids in the manifest.

    Idempotent through the manifest: an issue or rule that already carries an id
    is left alone, so re-running against a live instance neither duplicates rules
    nor fails.
    """
    classification = manifest.get("classification")
    if not classification:
        return

    issues = {issue["id"]: issue for issue in classification.get("issues", [])}
    rules = classification.get("rules", [])
    created_issues = 0
    created_rules = 0
    # Several issues commonly share one pin, and every lookup is a whole-test
    # query, so hold the responses for the duration of the run.
    lookups: dict[str, Any] = {}

    # An issue with no rules has no result to classify, so nothing creates it
    # as a side effect: it goes through the issues endpoint directly, with the
    # same unique-together fields the classify payload carries.
    with_rules = {rule["issue"] for rule in rules}
    projects: dict[str, int] = {}
    for issue in issues.values():
        if issue["id"] in with_rules or issue.get("issueId"):
            continue
        payload = {
            "title": issue["title"],
            "bug_key": issue.get("key") or None,
            "project": _project_of(
                manifest, issue, base_url, cookie_jar, fetch, projects
            ),
        }
        if issue.get("description"):
            payload["description"] = issue["description"]
        created = fetch(
            f"{base_url}/api/v2/issues/",
            method="POST",
            payload=payload,
            cookie_jar=cookie_jar,
        )
        issue["issueId"] = int(created["id"])
        issue["projectId"] = int(created["project"])
        issue["projectName"] = created["project_name"]
        created_issues += 1
        write_json(manifest_path, manifest, True)

    for rule in rules:
        if rule.get("ruleId"):
            continue
        issue = issues.get(rule["issue"])
        if issue is None:
            raise CliError(f"rule {rule['id']!r} names unknown issue {rule['issue']!r}")

        bundle, record = _seeded_bundle(manifest, rule["pin"])
        result = find_result(
            base_url, int(bundle["runId"]), record, cookie_jar, fetch, lookups
        )
        result_id = int(result["result_id"])
        project_id = result["project_id"]
        project_name = result["project_name"]

        # The first rule for an issue creates it; later rules reference the id the
        # backend handed back, so one issue can carry several differently-shaped
        # rules — which is what lets the suite compare their behaviour.
        if issue.get("issueId"):
            # An Issue belongs to exactly one project, and the endpoint rejects a
            # reference from a result in another one. Say so against the plan's
            # own ids: the backend's message names an issue id nobody wrote.
            if issue.get("projectId") not in (None, project_id):
                raise CliError(
                    f"issue {issue['id']!r} was created in project "
                    f"{issue['projectName']!r} but rule {rule['id']!r} classifies "
                    f"a result in {project_name!r}; an issue belongs to one "
                    "project, so split it into one issue per project"
                )
            issue_payload: Any = issue["issueId"]
        else:
            # Issue has UniqueConstraints over (project, title) and
            # (project, bug_key), and DRF makes every field of a unique-together
            # constraint required -- so both must be present even though the
            # endpoint overwrites `project` with the classified result's own and
            # an issue without an external reference has no bug key at all.
            # `null` is what "unlinked" looks like there; omitting it is a 400.
            issue_payload = {
                "title": issue["title"],
                "project": project_id,
                "bug_key": issue.get("key") or None,
            }
            if issue.get("description"):
                issue_payload["description"] = issue["description"]

        body = {
            "issue": issue_payload,
            "category": rule["category"],
            "expected": rule["expected"],
            "scope": rule["scope"],
            "matcher": matcher_for(rule.get("match") or []),
        }
        response = fetch(
            f"{base_url}/api/v2/results/{result_id}/classify/",
            method="POST",
            payload=body,
            cookie_jar=cookie_jar,
        )
        if not issue.get("issueId"):
            issue["issueId"] = int(response["issue_id"])
            issue["projectId"] = project_id
            issue["projectName"] = project_name
            created_issues += 1
        rule["ruleId"] = int(response["rule_id"])
        rule.setdefault("classifiedResultIds", []).append(result_id)
        created_rules += 1
        # Persist per rule, not once at the end. The ids are the only record
        # that these issues and rules exist -- nothing else ties an instance's
        # issue back to the plan that asked for it -- so a failure partway
        # through, with the manifest unwritten, strands everything created so
        # far: the re-run tries to create them again and the backend rejects the
        # duplicate. Writing as we go makes a failed run resumable instead.
        write_json(manifest_path, manifest, True)

    # A rule the plan wants inactive is created like any other and then
    # deactivated, so it exists on an open issue without stamping anything new.
    # Like closing, this is a collection action that counts what it moved, so a
    # re-run sends the same ids and reports nothing done.
    to_deactivate = [
        rule["ruleId"]
        for rule in rules
        if rule.get("active") is False and rule.get("ruleId")
    ]
    deactivated = 0
    if to_deactivate:
        summary = fetch(
            f"{base_url}/api/v2/issue_rules/deactivate/",
            method="POST",
            payload={"ids": to_deactivate},
            cookie_jar=cookie_jar,
        )
        deactivated = int(summary.get("updated") or 0)

    # Closing happens last: it deactivates the issue's rules, so a rule created
    # after the close would be silently inert.
    #
    # `close` is a *collection* action -- POST /issues/close/ with a list of ids,
    # not /issues/{id}/close/ -- and it reports how many issues it actually
    # moved. That count is what a re-run should report, so an already-closed
    # issue needs no state check of its own.
    to_close = [
        issue["issueId"]
        for issue in issues.values()
        if issue.get("close") and issue.get("issueId")
    ]
    closed = 0
    if to_close:
        summary = fetch(
            f"{base_url}/api/v2/issues/close/",
            method="POST",
            payload={"ids": to_close},
            cookie_jar=cookie_jar,
        )
        closed = int(summary.get("updated") or 0)

    write_json(manifest_path, manifest, True)
    parts = []
    if created_issues:
        parts.append(f"created {created_issues} issues")
    if created_rules:
        parts.append(f"created {created_rules} rules")
    if deactivated:
        parts.append(f"deactivated {deactivated} rules")
    if closed:
        parts.append(f"closed {closed}")
    if not parts:
        parts.append("classification already applied")
    console.print(f"[green]✓[/] {'; '.join(parts)}")
