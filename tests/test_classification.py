from __future__ import annotations

from pathlib import Path
from unittest.mock import ANY

import pytest

from core.bundle import FixtureSpec, apply_mix, collect_leaf_tests, generate_bundle
from core.classification import Pin, build_classification, classification_manifest
from core.classify_api import find_result, matcher_for
from core.common import CliError, read_json
from core.discovery import discover_fixtures
from core.planning import MixValue


def _leaves(bundle_dir: Path) -> list[dict]:
    return collect_leaf_tests(read_json(bundle_dir / "bublik.json"))


def _build(tmp_path: Path, conclusion: str = "nok-error") -> tuple[Path, object]:
    fixture = discover_fixtures()["net-drv-ts"]
    bundle_dir = tmp_path / "run"
    spec = FixtureSpec(
        id="net-drv-ts-run",
        fixture_name=fixture.name,
        fixture_id="e2e:net-drv-ts",
        project=fixture.project,
        conclusion=conclusion,
        mix_name="test",
        run_date="2026-04-25",
        tags={"ordinal": "1"},
    )
    generate_bundle(fixture, spec, bundle_dir, pretty=True)
    return bundle_dir, spec


# --------------------------------------------------------------------------
# Pins survive the mix
# --------------------------------------------------------------------------


def test_pinned_leaf_keeps_its_authored_verdicts(tmp_path: Path) -> None:
    bundle_dir, _ = _build(tmp_path)
    pin = Pin(
        id="rx",
        fixture="net-drv-ts",
        test="rx_mode",
        verdicts=("RX mode negotiation timed out",),
    )

    records = apply_mix(
        bundle_dir,
        [MixValue("unexpectedFailed", 80, True)],
        "nok-error",
        pretty=True,
        pins=[pin],
    )

    assert records, "the pin should have selected at least one leaf"
    pinned = {(r["test"], r["tin"]) for r in records}
    for leaf in _leaves(bundle_dir):
        if (leaf["name"], leaf.get("tin")) in pinned:
            assert leaf["obtained"]["result"]["verdicts"] == [
                "RX mode negotiation timed out"
            ]
            assert leaf["obtained"]["result"]["status"] == "FAILED"


def test_mix_never_overwrites_a_pinned_leaf(tmp_path: Path) -> None:
    """A percentage large enough to cover the whole run must still skip pins."""
    bundle_dir, _ = _build(tmp_path)
    pin = Pin(
        id="sr",
        fixture="net-drv-ts",
        test="send_receive",
        status="SKIPPED",
        unexpected=False,
        verdicts=(),
        iterations=(0,),
    )

    records = apply_mix(
        bundle_dir,
        [MixValue("unexpectedFailed", 95, True)],
        "nok-error",
        pretty=True,
        pins=[pin],
    )

    assert len(records) == 1
    leaves = {(leaf["name"], leaf.get("tin")): leaf for leaf in _leaves(bundle_dir)}
    leaf = leaves[("send_receive", 0)]
    assert leaf["obtained"]["result"]["status"] == "SKIPPED"
    assert leaf["obtained"]["result"]["verdicts"] == []


def test_the_same_pin_resolves_identically_in_two_runs(tmp_path: Path) -> None:
    """Cross-run identity is what lets a rule written on one run assert on another."""
    signatures = []
    for index in range(2):
        bundle_dir, _ = _build(tmp_path / f"r{index}")
        records = apply_mix(
            bundle_dir,
            [MixValue("unexpectedFailed", 80, True)],
            "nok-error",
            pretty=True,
            pins=[
                Pin(
                    id="rx",
                    fixture="net-drv-ts",
                    test="rx_mode",
                    verdicts=("RX mode negotiation timed out",),
                )
            ],
        )
        signatures.append(
            [(r["test"], r["tin"], tuple(sorted(r["params"]))) for r in records]
        )

    assert signatures[0] == signatures[1]


def test_a_pin_that_selects_nothing_is_an_error(tmp_path: Path) -> None:
    bundle_dir, _ = _build(tmp_path)
    with pytest.raises(CliError, match="selects no leaf"):
        apply_mix(
            bundle_dir,
            [],
            "nok-error",
            pretty=True,
            pins=[Pin(id="nope", fixture="net-drv-ts", test="no_such_test")],
        )


# --------------------------------------------------------------------------
# Plan validation
# --------------------------------------------------------------------------


def _plan(**overrides) -> dict:
    base = {
        "pins": [
            {
                "id": "rx",
                "fixture": "net-drv-ts",
                "test": "rx_mode",
                "verdicts": ["timed out"],
            }
        ],
        "issues": [{"id": "known", "title": "Known"}],
        "rules": [{"id": "r1", "issue": "known", "pin": "rx", "match": ["verdicts"]}],
    }
    base.update(overrides)
    return base


def test_classification_plan_resolves_cross_references() -> None:
    plan = build_classification(_plan())
    assert plan.rules[0].disposition() is True  # known-issue defaults to expected
    assert plan.pin_by_id("rx").verdicts == ("timed out",)


def test_rule_naming_an_unknown_issue_is_rejected() -> None:
    with pytest.raises(CliError, match="unknown issue"):
        build_classification(_plan(rules=[{"id": "r1", "issue": "ghost", "pin": "rx"}]))


def test_rule_matching_verdicts_needs_a_pin_that_authors_them() -> None:
    with pytest.raises(CliError, match="authors none"):
        build_classification(
            _plan(
                pins=[{"id": "rx", "fixture": "net-drv-ts", "test": "rx_mode"}],
                rules=[
                    {"id": "r1", "issue": "known", "pin": "rx", "match": ["verdicts"]}
                ],
            )
        )


def test_unknown_match_dimension_is_rejected() -> None:
    with pytest.raises(CliError, match="unknown dimension"):
        build_classification(
            _plan(
                rules=[{"id": "r1", "issue": "known", "pin": "rx", "match": ["tagz"]}]
            )
        )


def test_duplicate_ids_are_rejected() -> None:
    with pytest.raises(CliError, match="duplicate classification issue"):
        build_classification(
            _plan(issues=[{"id": "known", "title": "A"}, {"id": "known", "title": "B"}])
        )


def test_explicit_expected_overrides_the_category_default() -> None:
    plan = build_classification(
        _plan(
            rules=[
                {
                    "id": "r1",
                    "issue": "known",
                    "pin": "rx",
                    "category": "known-issue",
                    "expected": False,
                }
            ]
        )
    )
    assert plan.rules[0].disposition() is False


def test_an_issue_whose_rules_span_fixtures_is_rejected() -> None:
    """One fixture is one project, and an Issue belongs to exactly one project."""
    with pytest.raises(CliError, match="across fixtures"):
        build_classification(
            {
                "pins": [
                    {"id": "rx", "fixture": "net-drv-ts", "test": "rx_mode"},
                    {"id": "tx", "fixture": "dpdk-ethdev-ts", "test": "tx_burst"},
                ],
                "issues": [
                    {
                        "id": "shared",
                        "title": "Shared",
                        "rules": [{"pin": "rx"}, {"pin": "tx"}],
                    }
                ],
            }
        )


def test_an_absent_section_is_empty() -> None:
    assert build_classification(None).is_empty()


# --------------------------------------------------------------------------
# Manifest projection
# --------------------------------------------------------------------------


def test_manifest_splits_bundles_into_import_waves() -> None:
    plan = build_classification(_plan())
    bundles = [
        {"id": "seed-a", "importVia": "api", "pinnedResults": [{"pin": "rx"}]},
        {"id": "seed-a2", "importVia": "api", "pinnedResults": [{"pin": "rx"}]},
        {"id": "later", "importVia": "ui", "pinnedResults": [{"pin": "rx"}]},
    ]

    rendered = classification_manifest(plan, bundles)

    assert rendered is not None
    pin = rendered["pins"][0]
    assert pin["seededIn"] == ["seed-a", "seed-a2"]
    assert pin["appliesTo"] == ["later"]
    assert rendered["issues"][0]["issueId"] is None
    assert rendered["rules"][0]["ruleId"] is None
    # Filled in by --setup-classification, from the result actually classified.
    assert rendered["issues"][0]["projectId"] is None
    assert rendered["issues"][0]["projectName"] is None


def test_a_pin_forcing_many_leaves_lists_each_run_once() -> None:
    plan = build_classification(_plan())
    bundles = [
        {
            "id": "seed",
            "importVia": "api",
            "pinnedResults": [{"pin": "rx"}, {"pin": "rx"}, {"pin": "rx"}],
        }
    ]

    rendered = classification_manifest(plan, bundles)

    assert rendered["pins"][0]["seededIn"] == ["seed"]


def test_no_classification_section_means_no_manifest_block() -> None:
    assert classification_manifest(build_classification(None), []) is None


def test_the_manifest_carries_an_issue_description() -> None:
    """The description is authored in the plan and only ever passes through.

    Nothing derives or defaults it: an issue the plan leaves bare reaches the
    manifest as null, which is what the empty-body fixtures rely on.
    """
    plan = build_classification(
        _plan(
            issues=[
                {"id": "known", "title": "Known", "description": "**Bad** thing.\n"},
                {"id": "bare", "title": "Bare", "fixture": "net-drv-ts"},
            ]
        )
    )
    bundles = [{"id": "seed", "importVia": "api", "pinnedResults": [{"pin": "rx"}]}]

    issues = classification_manifest(plan, bundles)["issues"]

    assert issues[0]["description"] == "**Bad** thing.\n"
    assert issues[1]["description"] is None


# --------------------------------------------------------------------------
# The classify matcher
# --------------------------------------------------------------------------


def test_matcher_suppresses_every_dimension_for_a_test_only_rule() -> None:
    assert matcher_for([]) == {"parameters": {}, "verdicts": [], "tags": []}


def test_matcher_omits_the_dimensions_it_keeps() -> None:
    """An omitted key tells the backend to capture that dimension from the result."""
    assert matcher_for(["verdicts"]) == {"parameters": {}, "tags": []}
    assert matcher_for(["parameters", "verdicts"]) == {"tags": []}
    assert matcher_for(["parameters", "verdicts", "tags"]) == {}


# --------------------------------------------------------------------------
# Resolving a pinned leaf to an imported result
# --------------------------------------------------------------------------


def test_find_result_matches_on_a_parameter_subset() -> None:
    record = {"pin": "rx", "test": "rx_mode", "params": {"mode": "promisc"}}

    def fetch(url: str, **_: object) -> dict:
        assert "test_name=rx_mode" in url
        # parent_id would filter by the leaf's immediate parent package, not the
        # run, so it must not be sent.
        assert "parent_id" not in url
        return {
            "results": [
                {"result_id": 1, "run_id": 7, "parameters": ["mode=allmulti"]},
                {
                    "result_id": 2,
                    "run_id": 7,
                    "parameters": ["mode=promisc", "env=lab"],
                },
            ]
        }

    result = find_result("http://host", 7, record, Path("cookies"), fetch)

    assert result["result_id"] == 2


def test_find_result_ignores_matching_results_from_other_runs() -> None:
    """The query spans every run, so the run filter is applied here."""
    record = {"pin": "rx", "test": "rx_mode", "params": {"mode": "promisc"}}

    def fetch(_url: str, **_: object) -> dict:
        return {
            "results": [
                {"result_id": 1, "run_id": 99, "parameters": ["mode=promisc"]},
                {"result_id": 2, "run_id": 7, "parameters": ["mode=promisc"]},
            ]
        }

    result = find_result("http://host", 7, record, Path("cookies"), fetch)

    assert result["result_id"] == 2


def test_find_result_reports_the_pin_when_the_run_lacks_the_test() -> None:
    record = {"pin": "rx", "test": "rx_mode", "params": {"mode": "promisc"}}

    def fetch(_url: str, **_: object) -> dict:
        return {"results": [{"result_id": 1, "run_id": 99, "parameters": []}]}

    with pytest.raises(CliError, match="has no result for test"):
        find_result("http://host", 7, record, Path("cookies"), fetch)


def test_find_result_reports_the_pin_when_no_parameters_match() -> None:
    record = {"pin": "rx", "test": "rx_mode", "params": {"mode": "promisc"}}

    def fetch(_url: str, **_: object) -> dict:
        return {
            "results": [{"result_id": 1, "run_id": 7, "parameters": ["mode=allmulti"]}]
        }

    with pytest.raises(CliError, match="pin 'rx'"):
        find_result("http://host", 7, record, Path("cookies"), fetch)


# --------------------------------------------------------------------------
# The tri-state disposition
# --------------------------------------------------------------------------


def test_an_explicit_null_expected_is_a_marker_not_a_category_default() -> None:
    """`expected: null` must survive as "marked, undecided"."""
    plan = build_classification(
        _plan(
            rules=[
                {
                    "id": "r1",
                    "issue": "known",
                    "pin": "rx",
                    "category": "known-issue",  # would default to True
                    "expected": None,
                }
            ]
        )
    )
    assert plan.rules[0].disposition() is None


def test_an_omitted_expected_takes_the_category_default() -> None:
    plan = build_classification(
        _plan(
            rules=[
                {"id": "r1", "issue": "known", "pin": "rx", "category": "flaky"},
                {
                    "id": "r2",
                    "issue": "known",
                    "pin": "rx",
                    "category": "product-defect",
                },
            ]
        )
    )
    assert plan.rules[0].disposition() is True
    assert plan.rules[1].disposition() is False


def test_plan_file_keeps_an_explicit_null_expected(tmp_path: Path) -> None:
    """End to end: YAML `expected: null` must reach the domain as None."""
    from core.plan_file import load_plan

    path = tmp_path / "plan.yaml"
    path.write_text(
        """
version: 1
classification:
  pins:
    - id: rx
      fixture: net-drv-ts
      test: rx_mode
  issues:
    - id: known
      title: Known
  rules:
    - id: marked
      issue: known
      pin: rx
      category: known-issue
      expected: null
    - id: defaulted
      issue: known
      pin: rx
      category: known-issue
days:
  2026-04-20:
    - net-drv-ts.ok=1
""",
        encoding="utf-8",
    )

    plan = build_classification(load_plan(path).classification_spec())

    by_id = {rule.id: rule for rule in plan.rules}
    assert by_id["marked"].disposition() is None
    assert by_id["defaulted"].disposition() is True


# --------------------------------------------------------------------------
# Applying the plan against an instance
# --------------------------------------------------------------------------


def _row(result_id: int, project_id: int = 3) -> dict:
    """One row as ``/results/?test_name=`` returns it, project fields included."""
    return {
        "result_id": result_id,
        "run_id": 7,
        "parameters": [],
        "project_id": project_id,
        "project_name": f"tsf/project-{project_id}",
    }


def _classify_manifest(tmp_path: Path) -> tuple[dict, Path]:
    manifest = {
        "bundles": [
            {
                "id": "seed",
                "importVia": "api",
                "runId": 7,
                "pinnedResults": [
                    {"pin": "rx", "test": "rx_mode", "tin": 0, "params": {}}
                ],
            }
        ],
        "classification": {
            "pins": [{"id": "rx"}],
            "issues": [
                {"id": "known", "title": "Known", "close": True, "issueId": None}
            ],
            "rules": [
                {
                    "id": "r1",
                    "issue": "known",
                    "pin": "rx",
                    "category": "known-issue",
                    "expected": True,
                    "scope": "future",
                    "match": [],
                    "ruleId": None,
                }
            ],
        },
    }
    return manifest, tmp_path / "manifest.json"


def test_apply_classification_creates_then_closes(tmp_path: Path) -> None:
    from core.classify_api import apply_classification

    calls: list[tuple[str, str, object]] = []

    def fetch(
        url: str,
        *,
        method: str = "GET",
        payload: dict | None = None,
        **_: object,
    ) -> dict:
        calls.append((method, url, payload))
        if "results/?" in url:
            return {"results": [_row(42)]}
        if url.endswith("/classify/"):
            return {"issue_id": 3, "rule_id": 9}
        if url.endswith("/issues/close/"):
            return {"requested": 1, "updated": 1, "unchanged": 0, "not_found": 0}
        return {}

    manifest, path = _classify_manifest(tmp_path)
    apply_classification(manifest, path, "http://host", Path("jar"), fetch)

    classification = manifest["classification"]
    assert classification["issues"][0]["issueId"] == 3
    assert classification["rules"][0]["ruleId"] == 9
    assert classification["rules"][0]["classifiedResultIds"] == [42]
    # close is a collection action, taking the ids in the body.
    assert ("POST", "http://host/api/v2/issues/close/", {"ids": [3]}) in calls


def test_the_classify_payload_carries_the_results_project(tmp_path: Path) -> None:
    """The endpoint validates the inline issue before filling its project in.

    ``ClassifyRequestSerializer.validate_issue`` runs the whole ``IssueSerializer``
    over the body, where ``project`` is a required FK, and only then does the view
    overwrite it with the classified result's own. Sending the result's project is
    what gets past that, and it can never disagree with what the backend picks.
    """
    from core.classify_api import apply_classification

    bodies: list[dict] = []

    def fetch(
        url: str,
        *,
        method: str = "GET",
        payload: dict | None = None,
        **_: object,
    ) -> dict:
        if "results/?" in url:
            return {"results": [_row(42, project_id=5)]}
        if url.endswith("/classify/"):
            bodies.append(payload or {})
            return {"issue_id": 3, "rule_id": 9}
        if url.endswith("/issues/3/"):
            return {"state": "open"}
        return {}

    manifest, path = _classify_manifest(tmp_path)
    apply_classification(manifest, path, "http://host", Path("jar"), fetch)

    # bug_key is sent even though the plan's issue has no external key: it
    # shares a unique-together constraint with project, so DRF requires it.
    assert bodies[0]["issue"] == {
        "title": "Known",
        "project": 5,
        "bug_key": None,
    }
    issue = manifest["classification"]["issues"][0]
    assert (issue["projectId"], issue["projectName"]) == (5, "tsf/project-5")


def test_the_classify_payload_carries_the_issue_description(tmp_path: Path) -> None:
    """Sent only when the plan wrote one, and only on the request that creates.

    An issue that already carries an ``issueId`` is referenced by id, so a
    description edited after the issue exists never reaches the instance --
    changing one needs a stack reset, the same as changing a pin.
    """
    from core.classify_api import apply_classification

    bodies: list[dict] = []

    def fetch(
        url: str,
        *,
        method: str = "GET",
        payload: dict | None = None,
        **_: object,
    ) -> dict:
        if "results/?" in url:
            return {"results": [_row(42)]}
        if url.endswith("/classify/"):
            bodies.append(payload or {})
            return {"issue_id": 3, "rule_id": 9}
        return {}

    manifest, path = _classify_manifest(tmp_path)
    manifest["classification"]["issues"][0]["description"] = "**Bad** thing.\n"
    apply_classification(manifest, path, "http://host", Path("jar"), fetch)

    assert bodies[0]["issue"]["description"] == "**Bad** thing.\n"


def test_an_issue_without_a_description_omits_the_key(tmp_path: Path) -> None:
    """Not an empty string: the column is nullable and null is what "none" is."""
    from core.classify_api import apply_classification

    bodies: list[dict] = []

    def fetch(
        url: str,
        *,
        method: str = "GET",
        payload: dict | None = None,
        **_: object,
    ) -> dict:
        if "results/?" in url:
            return {"results": [_row(42)]}
        if url.endswith("/classify/"):
            bodies.append(payload or {})
            return {"issue_id": 3, "rule_id": 9}
        return {}

    manifest, path = _classify_manifest(tmp_path)
    manifest["classification"]["issues"][0]["description"] = None
    apply_classification(manifest, path, "http://host", Path("jar"), fetch)

    assert "description" not in bodies[0]["issue"]


def test_ids_survive_a_failure_partway_through(tmp_path: Path) -> None:
    """Nothing but the manifest ties an instance's issue back to the plan.

    Written only at the end, a run that dies on a later rule strands everything
    it already created: the re-run tries to create the same issues again and the
    backend rejects them as duplicates.
    """
    from core.classify_api import apply_classification

    def fetch(url: str, *, method: str = "GET", **_: object) -> dict:
        if "results/?" in url:
            return {"results": [_row(42)]}
        if url.endswith("/classify/"):
            return {"issue_id": 3, "rule_id": 9}
        raise CliError("boom")

    manifest, path = _classify_manifest(tmp_path)
    with pytest.raises(CliError, match="boom"):
        apply_classification(manifest, path, "http://host", Path("jar"), fetch)

    written = read_json(path)
    assert written["classification"]["issues"][0]["issueId"] == 3
    assert written["classification"]["rules"][0]["ruleId"] == 9


def test_apply_classification_is_idempotent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A re-run must neither duplicate rules nor claim work it did not do."""
    from core.classify_api import apply_classification

    calls: list[tuple[str, str]] = []

    def fetch(url: str, *, method: str = "GET", **_: object) -> dict:
        calls.append((method, url))
        if url.endswith("/issues/close/"):
            # Already closed: the endpoint counts it as unchanged.
            return {"requested": 1, "updated": 0, "unchanged": 1, "not_found": 0}
        return {}

    manifest, path = _classify_manifest(tmp_path)
    manifest["classification"]["issues"][0]["issueId"] = 3
    manifest["classification"]["rules"][0]["ruleId"] = 9

    apply_classification(manifest, path, "http://host", Path("jar"), fetch)

    assert not [c for c in calls if c[1].endswith("/classify/")]
    assert "already applied" in capsys.readouterr().err


# --------------------------------------------------------------------------
# Rules nested under their issue
# --------------------------------------------------------------------------


def test_nested_rules_are_lifted_and_linked_to_their_issue() -> None:
    plan = build_classification(
        {
            "pins": [
                {"id": "rx", "fixture": "net-drv-ts", "test": "rx_mode"},
            ],
            "issues": [
                {
                    "id": "known",
                    "title": "Known",
                    "rules": [
                        {"pin": "rx", "category": "known-issue"},
                        {"pin": "rx", "category": "product-defect"},
                    ],
                }
            ],
        }
    )

    assert [rule.issue for rule in plan.rules] == ["known", "known"]
    # An omitted id is derived, which at this scale is most of the file.
    assert [rule.id for rule in plan.rules] == ["known-1", "known-2"]


def test_a_nested_rule_may_still_name_its_own_id() -> None:
    plan = build_classification(
        {
            "pins": [{"id": "rx", "fixture": "net-drv-ts", "test": "rx_mode"}],
            "issues": [
                {
                    "id": "known",
                    "title": "Known",
                    "rules": [{"id": "explicit", "pin": "rx"}],
                }
            ],
        }
    )

    assert plan.rules[0].id == "explicit"


def test_nested_and_flat_rules_can_be_mixed() -> None:
    plan = build_classification(
        {
            "pins": [{"id": "rx", "fixture": "net-drv-ts", "test": "rx_mode"}],
            "issues": [
                {"id": "a", "title": "A", "rules": [{"pin": "rx"}]},
                {"id": "b", "title": "B"},
            ],
            "rules": [{"id": "flat", "issue": "b", "pin": "rx"}],
        }
    )

    assert {rule.id for rule in plan.rules} == {"a-1", "flat"}


def test_a_derived_id_colliding_with_a_flat_one_is_rejected() -> None:
    with pytest.raises(CliError, match="duplicate classification rule"):
        build_classification(
            {
                "pins": [{"id": "rx", "fixture": "net-drv-ts", "test": "rx_mode"}],
                "issues": [{"id": "a", "title": "A", "rules": [{"pin": "rx"}]}],
                "rules": [{"id": "a-1", "issue": "a", "pin": "rx"}],
            }
        )


# --------------------------------------------------------------------------
# Guarding against a run that predates its pin
# --------------------------------------------------------------------------


def _stale_fetch(live_verdicts: list[str]):
    def fetch(_url: str, **_: object) -> dict:
        return {
            "results": [
                {
                    "result_id": 5,
                    "run_id": 7,
                    "parameters": ["mtu=9000"],
                    "obtained_result": {"verdicts": live_verdicts},
                }
            ]
        }

    return fetch


def test_a_result_missing_the_pins_verdicts_is_rejected() -> None:
    """Silently classifying it would create a broader rule than declared.

    A rule that asked to match on verdicts, given none to match on, becomes
    test-only and stamps every iteration of that test.
    """
    record = {
        "pin": "mtu",
        "test": "mtu_tcp",
        "params": {"mtu": "9000"},
        "verdicts": ["TCP fragment lost above MTU"],
    }

    with pytest.raises(CliError, match="older than the pin"):
        find_result("http://host", 7, record, Path("jar"), _stale_fetch([]))


def test_a_result_carrying_the_pins_verdicts_resolves() -> None:
    record = {
        "pin": "mtu",
        "test": "mtu_tcp",
        "params": {"mtu": "9000"},
        "verdicts": ["TCP fragment lost above MTU"],
    }
    fetch = _stale_fetch(["TCP fragment lost above MTU"])

    assert find_result("http://host", 7, record, Path("jar"), fetch)["result_id"] == 5


def test_extra_verdicts_on_the_result_are_fine() -> None:
    """The pin's verdicts are a subset check, matching the backend's matcher."""
    record = {
        "pin": "mtu",
        "test": "mtu_tcp",
        "params": {"mtu": "9000"},
        "verdicts": ["TCP fragment lost above MTU"],
    }
    fetch = _stale_fetch(["TCP fragment lost above MTU", "Something else"])

    assert find_result("http://host", 7, record, Path("jar"), fetch)["result_id"] == 5


def test_a_pin_authoring_no_verdicts_skips_the_check() -> None:
    record = {"pin": "p", "test": "mtu_tcp", "params": {"mtu": "9000"}}

    result = find_result("http://host", 7, record, Path("jar"), _stale_fetch([]))

    assert result["result_id"] == 5


# --------------------------------------------------------------------------
# Issues without rules
# --------------------------------------------------------------------------


def test_an_issue_without_rules_needs_a_fixture() -> None:
    """Its project would otherwise follow from nothing."""
    with pytest.raises(CliError, match="issue 'lonely' has no rules"):
        build_classification(
            _plan(
                issues=[
                    {"id": "known", "title": "Known"},
                    {"id": "lonely", "title": "Lonely"},
                ]
            )
        )


def test_an_issue_without_rules_takes_its_fixture_from_the_plan() -> None:
    plan = build_classification(
        _plan(
            issues=[
                {"id": "known", "title": "Known"},
                {"id": "lonely", "title": "Lonely", "fixture": "dpdk-ethdev-ts"},
            ]
        )
    )

    assert plan.issue_by_id("lonely").fixture == "dpdk-ethdev-ts"


def test_an_issue_fixture_must_agree_with_its_rules() -> None:
    with pytest.raises(CliError, match="issue 'known' names fixture 'dpdk-ethdev-ts'"):
        build_classification(
            _plan(
                issues=[{"id": "known", "title": "Known", "fixture": "dpdk-ethdev-ts"}]
            )
        )


def test_an_issue_with_rules_derives_its_fixture() -> None:
    plan = build_classification(_plan())

    assert plan.issue_by_id("known").fixture == "net-drv-ts"


def test_plan_file_accepts_a_rule_less_issue(tmp_path: Path) -> None:
    from core.plan_file import load_plan

    path = tmp_path / "plan.yaml"
    path.write_text(
        """
version: 1
classification:
  issues:
    - id: lonely
      title: Lonely
      fixture: net-drv-ts
days:
  2026-04-20:
    - net-drv-ts.ok=1
""",
        encoding="utf-8",
    )

    plan = build_classification(load_plan(path).classification_spec())

    assert plan.issue_by_id("lonely").fixture == "net-drv-ts"
    assert plan.rules == ()


@pytest.mark.parametrize("command", ["plan", "generate"])
def test_a_rule_less_issue_in_an_unknown_fixture_fails_at_plan_time(
    tmp_path: Path, command: str
) -> None:
    """A typo in the fixture is caught before anything is generated or seeded."""
    from typer.testing import CliRunner

    from cli import app

    path = tmp_path / "plan.yaml"
    path.write_text(
        """
version: 1
classification:
  issues:
    - id: lonely
      title: Lonely
      fixture: net-drv
days:
  2026-04-20:
    - net-drv-ts.ok=1
""",
        encoding="utf-8",
    )
    schema = tmp_path / "schema.json"
    schema.write_text('{"type": "object"}', encoding="utf-8")
    extra = {
        "plan": [],
        "generate": [
            "--publish-dir",
            str(tmp_path / "publish"),
            "--manifest",
            str(tmp_path / "manifest.json"),
            "--run-log-schema",
            str(schema),
            "--meta-data-schema",
            str(schema),
        ],
    }[command]

    result = CliRunner().invoke(app, [command, "--plan", str(path), *extra])

    assert result.exit_code == 1, result.output
    assert "issue 'lonely' names unknown fixture 'net-drv'" in " ".join(
        result.output.split()
    )
    assert not (tmp_path / "manifest.json").exists()


# --------------------------------------------------------------------------
# Rules created and then deactivated
# --------------------------------------------------------------------------


def test_plan_file_carries_a_rules_active_flag(tmp_path: Path) -> None:
    from core.plan_file import load_plan

    path = tmp_path / "plan.yaml"
    path.write_text(
        """
version: 1
classification:
  pins:
    - id: rx
      fixture: net-drv-ts
      test: rx_mode
  issues:
    - id: known
      title: Known
      rules:
        - id: paused
          pin: rx
          active: false
        - id: live
          pin: rx
days:
  2026-04-20:
    - net-drv-ts.ok=1
""",
        encoding="utf-8",
    )

    plan = build_classification(load_plan(path).classification_spec())

    by_id = {rule.id: rule for rule in plan.rules}
    assert by_id["paused"].active is False
    assert by_id["live"].active is True


def test_an_inactive_oneoff_rule_is_rejected() -> None:
    """A oneoff rule is created inactive, so there is nothing to deactivate."""
    with pytest.raises(CliError, match="rule 'r1' is oneoff"):
        build_classification(
            _plan(
                rules=[
                    {
                        "id": "r1",
                        "issue": "known",
                        "pin": "rx",
                        "scope": "oneoff",
                        "active": False,
                    }
                ]
            )
        )


def test_an_inactive_rule_on_a_closed_issue_is_rejected() -> None:
    """Closing deactivates every rule; the flag is for rules on open issues."""
    with pytest.raises(CliError, match="rule 'r1' .* issue 'known' closes"):
        build_classification(
            _plan(
                issues=[{"id": "known", "title": "Known", "close": True}],
                rules=[{"id": "r1", "issue": "known", "pin": "rx", "active": False}],
            )
        )


# --------------------------------------------------------------------------
# What the manifest records for both
# --------------------------------------------------------------------------


def test_the_manifest_records_issue_fixtures_and_rule_activity() -> None:
    from core.manifest_models import ClassificationManifest

    plan = build_classification(
        _plan(
            issues=[
                {"id": "known", "title": "Known"},
                {"id": "lonely", "title": "Lonely", "fixture": "dpdk-ethdev-ts"},
            ],
            rules=[
                {"id": "r1", "issue": "known", "pin": "rx"},
                {"id": "r2", "issue": "known", "pin": "rx", "active": False},
            ],
        )
    )
    bundles = [{"id": "seed", "importVia": "api", "pinnedResults": [{"pin": "rx"}]}]

    rendered = classification_manifest(plan, bundles)

    issues = {issue["id"]: issue for issue in rendered["issues"]}
    assert issues["known"]["fixture"] == "net-drv-ts"
    assert issues["lonely"]["fixture"] == "dpdk-ethdev-ts"
    assert [rule["active"] for rule in rendered["rules"]] == [True, False]
    ClassificationManifest.model_validate(rendered)


# --------------------------------------------------------------------------
# Applying rule-less issues and inactive rules
# --------------------------------------------------------------------------


def _shapes_manifest(tmp_path: Path) -> tuple[dict, Path]:
    """One rule-less issue and one rule deactivated after it exists."""
    manifest, path = _classify_manifest(tmp_path)
    manifest["bundles"][0]["fixture"] = "net-drv-ts"
    manifest["bundles"][0]["project"] = "tsf/net-drv"
    classification = manifest["classification"]
    classification["issues"] = [
        {"id": "known", "title": "Known", "close": False, "fixture": "net-drv-ts"},
        {
            "id": "lonely",
            "title": "Lonely",
            "description": "Nobody classified anything into this yet.",
            "key": "ref://E2E_BUGS/E2E-900",
            "close": False,
            "fixture": "net-drv-ts",
        },
    ]
    classification["rules"][0]["active"] = False
    return manifest, path


class _Instance:
    """A fake fetch that hands out ids and records every request."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, object]] = []
        # Collection actions count only what they move, like the backend's.
        self.moved: set[tuple[str, int]] = set()

    def __call__(
        self, url: str, *, method: str = "GET", payload: dict | None = None, **_: object
    ) -> object:
        self.calls.append((method, url.removeprefix("http://host"), payload))
        if url.endswith("/api/v2/projects/"):
            return [{"id": 4, "name": "tsf/other"}, {"id": 5, "name": "tsf/net-drv"}]
        if "results/?" in url:
            return {"results": [_row(42, project_id=5)]}
        if url.endswith("/classify/"):
            return {"issue_id": 3, "rule_id": 9}
        if url.endswith("/api/v2/issues/") and method == "POST":
            return {"id": 11, "project": 5, "project_name": "tsf/net-drv"}
        if url.endswith("/deactivate/") or url.endswith("/close/"):
            ids = [(url, i) for i in (payload or {})["ids"]]
            updated = [key for key in ids if key not in self.moved]
            self.moved.update(updated)
            return {"requested": len(ids), "updated": len(updated)}
        return {}

    def posts(self) -> list[tuple[str, object]]:
        return [
            (url, payload) for method, url, payload in self.calls if method == "POST"
        ]


def test_a_rule_less_issue_is_created_in_its_fixtures_project(tmp_path: Path) -> None:
    from core.classify_api import apply_classification

    manifest, path = _shapes_manifest(tmp_path)
    instance = _Instance()
    apply_classification(manifest, path, "http://host", Path("jar"), instance)

    assert (
        "/api/v2/issues/",
        {
            "title": "Lonely",
            "description": "Nobody classified anything into this yet.",
            "bug_key": "ref://E2E_BUGS/E2E-900",
            "project": 5,
        },
    ) in instance.posts()
    lonely = read_json(path)["classification"]["issues"][1]
    assert (lonely["issueId"], lonely["projectId"], lonely["projectName"]) == (
        11,
        5,
        "tsf/net-drv",
    )


def test_setup_creates_then_deactivates_then_closes(tmp_path: Path) -> None:
    """Deactivating needs the rule to exist; closing stays the last word."""
    from core.classify_api import apply_classification

    manifest, path = _shapes_manifest(tmp_path)
    manifest["classification"]["issues"][1]["close"] = True
    instance = _Instance()
    apply_classification(manifest, path, "http://host", Path("jar"), instance)

    assert instance.posts() == [
        ("/api/v2/issues/", ANY),
        ("/api/v2/results/42/classify/", ANY),
        ("/api/v2/issue_rules/deactivate/", {"ids": [9]}),
        ("/api/v2/issues/close/", {"ids": [11]}),
    ]


def test_a_re_run_creates_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from core.classify_api import apply_classification

    manifest, path = _shapes_manifest(tmp_path)
    instance = _Instance()
    apply_classification(manifest, path, "http://host", Path("jar"), instance)
    capsys.readouterr()
    first = len(instance.calls)

    apply_classification(read_json(path), path, "http://host", Path("jar"), instance)

    rerun = [url for _, url, _ in instance.calls[first:]]
    assert rerun == ["/api/v2/issue_rules/deactivate/"]
    assert "already applied" in capsys.readouterr().err


def test_a_rule_less_issue_in_an_unimported_fixture_is_named(tmp_path: Path) -> None:
    from core.classify_api import apply_classification

    manifest, path = _shapes_manifest(tmp_path)
    manifest["classification"]["issues"][1]["fixture"] = "dpdk-ethdev-ts"

    with pytest.raises(CliError, match="issue 'lonely' names fixture 'dpdk-ethdev-ts'"):
        apply_classification(manifest, path, "http://host", Path("jar"), _Instance())


def test_a_manifest_from_before_both_fields_still_validates_and_applies(
    tmp_path: Path,
) -> None:
    """`task e2e:seed` on a seeded stack applies the manifest it already has."""
    from core.classify_api import apply_classification
    from core.manifest_models import ClassificationManifest

    manifest, path = _classify_manifest(tmp_path)
    classification = manifest["classification"]
    classification["pins"] = [
        {
            "id": "rx",
            "fixture": "net-drv-ts",
            "test": "rx_mode",
            "status": "FAILED",
            "unexpected": True,
            "verdicts": [],
            "iterations": [],
            "conclusions": [],
            "seededIn": ["seed"],
            "appliesTo": [],
        }
    ]
    classification["issues"][0].update(description=None, key=None, close=False)
    ClassificationManifest.model_validate(classification)

    instance = _Instance()
    apply_classification(manifest, path, "http://host", Path("jar"), instance)

    assert [url for url, _ in instance.posts()] == ["/api/v2/results/42/classify/"]


def test_an_older_rule_less_issue_without_a_fixture_is_named(tmp_path: Path) -> None:
    from core.classify_api import apply_classification

    manifest, path = _classify_manifest(tmp_path)
    manifest["classification"]["issues"].append(
        {"id": "lonely", "title": "Lonely", "close": False}
    )

    with pytest.raises(CliError, match="issue 'lonely' has no rules and no fixture"):
        apply_classification(manifest, path, "http://host", Path("jar"), _Instance())
