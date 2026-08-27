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


def find_result_id(
    base_url: str,
    run_id: int,
    record: dict[str, Any],
    cookie_jar: Path,
    fetch: Any,
    cache: dict[str, Any] | None = None,
) -> int:
    """Resolve one pinned leaf to its result id in an imported run.

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
        if wanted <= got:
            return int(result["result_id"])
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

    for rule in rules:
        if rule.get("ruleId"):
            continue
        issue = issues.get(rule["issue"])
        if issue is None:
            raise CliError(f"rule {rule['id']!r} names unknown issue {rule['issue']!r}")

        bundle, record = _seeded_bundle(manifest, rule["pin"])
        result_id = find_result_id(
            base_url, int(bundle["runId"]), record, cookie_jar, fetch, lookups
        )

        # The first rule for an issue creates it; later rules reference the id the
        # backend handed back, so one issue can carry several differently-shaped
        # rules — which is what lets the suite compare their behaviour.
        if issue.get("issueId"):
            issue_payload: Any = issue["issueId"]
        else:
            issue_payload = {"title": issue["title"]}
            if issue.get("description"):
                issue_payload["description"] = issue["description"]
            if issue.get("key"):
                issue_payload["bug_key"] = issue["key"]

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
            created_issues += 1
        rule["ruleId"] = int(response["rule_id"])
        rule.setdefault("classifiedResultIds", []).append(result_id)
        created_rules += 1

    # Closing happens last: it deactivates the issue's rules, so a rule created
    # after the close would be silently inert. Re-reading the state first keeps
    # a re-run from reporting work it did not do.
    closed = 0
    for issue in issues.values():
        if not (issue.get("close") and issue.get("issueId")):
            continue
        current = fetch(
            f"{base_url}/api/v2/issues/{issue['issueId']}/",
            cookie_jar=cookie_jar,
        )
        if current.get("state") == "closed":
            continue
        fetch(
            f"{base_url}/api/v2/issues/{issue['issueId']}/close/",
            method="POST",
            payload={},
            cookie_jar=cookie_jar,
        )
        closed += 1

    write_json(manifest_path, manifest, True)
    parts = []
    if created_issues:
        parts.append(f"created {created_issues} issues")
    if created_rules:
        parts.append(f"created {created_rules} rules")
    if closed:
        parts.append(f"closed {closed}")
    if not parts:
        parts.append("classification already applied")
    console.print(f"[green]✓[/] {'; '.join(parts)}")
