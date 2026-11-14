"""Create the plan's classification issues and rules against imported runs.

This runs after the API import wave and before the held-back ``+ui`` bundles are
imported by the Playwright suite. That ordering is the whole point: a rule
created here did not exist when the ``+ui`` run was generated, so when Bublik
stamps that run on import it is demonstrably the *rule* doing the work and not
the fixture.

Rules are created through ``POST /results/classify/`` — the bulk endpoint the UI
itself calls — rather than by POSTing to ``/issue_rules/`` directly. That way the
fixture exercises the real triage path. The endpoint takes the matcher in full,
one per rule among the results, so what a rule should contain is read first from
``POST /results/classify/options/``: the test id, the parameters and verdicts the
result has, and the run's important tags. This module never reconstructs any of
those itself.

The options come in one of two shapes, and both are read: an object with the
run's ``tags`` and a ``tests`` list, or a list of rules, each carrying its own
``tags`` and the ``run_ids`` that, with the test id, tell the rule in the
matcher.

Each rule classifies its pin's single result, and Bublik otherwise applies rules
only on import, so the API-seeded runs -- imported before any rule existed --
would carry one stamp per rule and nothing else. The last stage therefore
re-applies the rules to every API-seeded run that carries pins, through
``POST /runs/{id}/apply_rules/`` (the UI's "Apply rules" button). That is what
gives an issue a realistic number of results across runs and days; the ``+ui``
run remains the proof that import-time stamping works on its own.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import urllib.parse

from core.classification import MATCH_DIMENSIONS
from core.common import CliError, console, write_json

# What a matcher dimension holds when it does not restrict the rule, so a rule
# keeps exactly the dimensions it names, filled from the classify options, and
# suppresses the rest. Verdicts are ignored with ``None``, not ``[]``: an empty
# list there is no verdict list at all, and the endpoint rejects it.
EMPTY_BY_DIMENSION: dict[str, Any] = {"parameters": {}, "verdicts": None, "tags": []}


def matcher_for(match: list[str], rule: dict[str, Any]) -> dict[str, Any]:
    """Build the classify matcher that keeps only ``match``.

    ``rule`` is the one rule the ``/results/classify/options/`` response
    proposes for the one result being classified, as :func:`classify_options`
    returns it: the test id, the parameters and the verdicts, and the run's
    default (important) tags -- the same values the single-result endpoint used
    to capture on its own. Its ``run_ids``, when the options gave them, go into
    the matcher as they are: they tell the rule, not what it matches.

    The test's verdicts are its results' distinct verdict lists, and a matcher
    restricting verdicts must give every one of them, getting a rule per list.
    With one result there is one list (``[]`` if it has no verdicts), so one
    rule. The lists are passed through untouched: the backend matches verdicts
    in order, so sorting them would make a rule that matches nothing.

    >>> rule = {
    ...     "test_id": 5,
    ...     "parameters": {"mode": "promisc"},
    ...     "verdicts": [["lost"]],
    ...     "tags": {"default": ["linux"], "allowed": ["linux", "x86"]},
    ... }
    >>> matcher_for([], rule)
    {'test_id': 5, 'parameters': {}, 'verdicts': None, 'tags': []}
    >>> matcher_for(["verdicts"], rule)
    {'test_id': 5, 'parameters': {}, 'verdicts': [['lost']], 'tags': []}
    >>> matcher_for(["parameters", "tags"], rule)
    {'test_id': 5, 'parameters': {'mode': 'promisc'}, 'verdicts': None, 'tags': ['linux']}
    >>> matcher_for([], {**rule, "run_ids": [7]})
    {'test_id': 5, 'run_ids': [7], 'parameters': {}, 'verdicts': None, 'tags': []}
    """
    captured: dict[str, Any] = {
        "parameters": dict(rule.get("parameters") or {}),
        "verdicts": [list(verdicts) for verdicts in rule.get("verdicts") or []],
        "tags": sorted((rule.get("tags") or {}).get("default") or []),
    }
    matcher: dict[str, Any] = {"test_id": int(rule["test_id"])}
    if rule.get("run_ids"):
        matcher["run_ids"] = [int(run_id) for run_id in rule["run_ids"]]
    for dimension in MATCH_DIMENSIONS:
        matcher[dimension] = (
            captured[dimension] if dimension in match else EMPTY_BY_DIMENSION[dimension]
        )
    return matcher


def rules_of(options: Any) -> list[dict[str, Any]]:
    """The rules a ``/results/classify/options/`` response proposes.

    A list is already one rule per entry, each with its own tags. The object
    shape holds the run's tags once and a test per entry, so each test gets
    those tags. Anything else proposes nothing.

    >>> rules_of([{"test_id": 5, "tags": {"default": ["a"]}}])
    [{'test_id': 5, 'tags': {'default': ['a']}}]
    >>> rules_of({"tags": {"default": ["a"]}, "tests": [{"test_id": 5}]})
    [{'test_id': 5, 'tags': {'default': ['a']}}]
    >>> rules_of(None)
    []
    """
    if isinstance(options, list):
        return [rule for rule in options if isinstance(rule, dict)]
    if isinstance(options, dict):
        tags = options.get("tags") or {}
        return [
            {**test, "tags": test.get("tags") or tags}
            for test in options.get("tests") or []
            if isinstance(test, dict)
        ]
    return []


def classify_options(
    base_url: str,
    result_id: int,
    cookie_jar: Path,
    fetch: Any,
    cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The rule the backend proposes for classifying ``result_id`` alone.

    The endpoint groups the results by test (and by run tags); with one result
    there is exactly one group, and anything else means the instance disagrees
    with the pin.
    """
    key = f"classify-options:{result_id}"
    if cache is not None and key in cache:
        return cache[key]
    options = fetch(
        f"{base_url}/api/v2/results/classify/options/",
        method="POST",
        payload={"result_ids": [result_id]},
        cookie_jar=cookie_jar,
    )
    rules = rules_of(options)
    if len(rules) != 1:
        raise CliError(
            f"result {result_id}: classify options proposed {len(rules)} rules "
            "for a single result; expected exactly one"
        )
    if cache is not None:
        cache[key] = rules[0]
    return rules[0]


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


def _updated_count(summary: dict[str, Any]) -> int:
    """How many ids a bulk issue action moved: the API lists them, and once
    counted them."""
    updated = summary.get("updated") or 0
    return len(updated) if isinstance(updated, list) else int(updated)


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
            # A new issue takes the classified result's project, so the payload
            # carries only what the plan says about the issue itself.
            issue_payload = {"title": issue["title"]}
            if issue.get("key"):
                issue_payload["bug_key"] = issue["key"]
            if issue.get("description"):
                issue_payload["description"] = issue["description"]

        # The bulk endpoint classifies a list of results under a list of
        # categories, one rule per (test, category). A plan rule is one pin and
        # one category, so both lists are singletons and exactly one rule comes
        # back.
        proposed = classify_options(base_url, result_id, cookie_jar, fetch, lookups)
        body = {
            "result_ids": [result_id],
            "issue": issue_payload,
            "categories": [rule["category"]],
            "expected": rule["expected"],
            "scope": rule["scope"],
            "matchers": [matcher_for(rule.get("match") or [], proposed)],
        }
        response = fetch(
            f"{base_url}/api/v2/results/classify/",
            method="POST",
            payload=body,
            cookie_jar=cookie_jar,
        )
        rule_ids = response.get("rule_ids") or []
        if len(rule_ids) != 1:
            raise CliError(
                f"rule {rule['id']!r}: classify returned {len(rule_ids)} rules for "
                "one result and one category; expected exactly one"
            )
        if not issue.get("issueId"):
            issue["issueId"] = int(response["issue_id"])
            issue["projectId"] = project_id
            issue["projectName"] = project_name
            created_issues += 1
        rule["ruleId"] = int(rule_ids[0])
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
        deactivated = _updated_count(summary)

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
        closed = _updated_count(summary)

    write_json(manifest_path, manifest, True)

    applied_runs, stamps = apply_rules_to_seeded_runs(
        manifest, manifest_path, base_url, cookie_jar, fetch, bool(created_rules)
    )

    parts = []
    if created_issues:
        parts.append(f"created {created_issues} issues")
    if created_rules:
        parts.append(f"created {created_rules} rules")
    if deactivated:
        parts.append(f"deactivated {deactivated} rules")
    if closed:
        parts.append(f"closed {closed}")
    if applied_runs:
        parts.append(f"applied rules to {applied_runs} runs ({stamps} stamps)")
    if not parts:
        parts.append("classification already applied")
    console.print(f"[green]✓[/] {'; '.join(parts)}")


def _pinned_api_bundles(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """The imported API bundles that carry pinned results.

    ``importVia`` is checked explicitly rather than inferred from ``runId``: a
    ``+ui`` bundle gets a ``runId`` too, once the Playwright suite has imported
    it, and that run was stamped on import -- re-applying would add nothing and
    would muddle the import-origin assertion the suite makes against it.

    A run without pins is left alone on purpose. Its results were never authored
    for a rule, and the suite reads exact unexpected counts off such runs; a
    test-only or parameters rule stamping one of them would suppress a result
    and shift the count.
    """
    return [
        bundle
        for bundle in manifest.get("bundles", [])
        if bundle.get("importVia", "api") == "api"
        and bundle.get("runId")
        and bundle.get("pinnedResults")
    ]


def apply_rules_to_seeded_runs(
    manifest: dict[str, Any],
    manifest_path: Path,
    base_url: str,
    cookie_jar: Path,
    fetch: Any,
    rules_changed: bool,
) -> tuple[int, int]:
    """Re-apply the active rules to every pinned API-seeded run.

    Runs last, after deactivation and closing: the endpoint applies only the
    active rules of open issues, and the stamps it lays are never removed, so
    running it any earlier would stamp results for rules the plan then switches
    off.

    Returns ``(runs applied to, stamps created)``. Each bundle records
    ``rulesApplied`` once its call succeeds, written per bundle so a failure
    partway through resumes where it stopped. ``rules_changed`` clears those
    marks first, so a rule created on this invocation reaches runs that were
    already stamped on an earlier one. The endpoint itself only adds missing
    stamps, so the mark saves round trips rather than guarding correctness.
    """
    bundles = _pinned_api_bundles(manifest)
    if rules_changed:
        for bundle in bundles:
            bundle.pop("rulesApplied", None)

    applied = 0
    stamps = 0
    for bundle in bundles:
        if bundle.get("rulesApplied"):
            continue
        summary = fetch(
            f"{base_url}/api/v2/runs/{int(bundle['runId'])}/apply_rules/",
            method="POST",
            payload={},
            cookie_jar=cookie_jar,
        )
        stamps += int((summary or {}).get("stamps_created") or 0)
        bundle["rulesApplied"] = True
        applied += 1
        write_json(manifest_path, manifest, True)
    return applied, stamps
