from __future__ import annotations

from pathlib import Path

import pytest

from core.bundle import FixtureSpec, apply_mix, collect_leaf_tests, generate_bundle
from core.classification import Pin, build_classification, classification_manifest
from core.classify_api import find_result_id, matcher_for
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


def test_find_result_id_matches_on_a_parameter_subset() -> None:
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

    assert find_result_id("http://host", 7, record, Path("cookies"), fetch) == 2


def test_find_result_id_ignores_matching_results_from_other_runs() -> None:
    """The query spans every run, so the run filter is applied here."""
    record = {"pin": "rx", "test": "rx_mode", "params": {"mode": "promisc"}}

    def fetch(_url: str, **_: object) -> dict:
        return {
            "results": [
                {"result_id": 1, "run_id": 99, "parameters": ["mode=promisc"]},
                {"result_id": 2, "run_id": 7, "parameters": ["mode=promisc"]},
            ]
        }

    assert find_result_id("http://host", 7, record, Path("cookies"), fetch) == 2


def test_find_result_id_reports_the_pin_when_the_run_lacks_the_test() -> None:
    record = {"pin": "rx", "test": "rx_mode", "params": {"mode": "promisc"}}

    def fetch(_url: str, **_: object) -> dict:
        return {"results": [{"result_id": 1, "run_id": 99, "parameters": []}]}

    with pytest.raises(CliError, match="has no result for test"):
        find_result_id("http://host", 7, record, Path("cookies"), fetch)


def test_find_result_id_reports_the_pin_when_no_parameters_match() -> None:
    record = {"pin": "rx", "test": "rx_mode", "params": {"mode": "promisc"}}

    def fetch(_url: str, **_: object) -> dict:
        return {
            "results": [{"result_id": 1, "run_id": 7, "parameters": ["mode=allmulti"]}]
        }

    with pytest.raises(CliError, match="pin 'rx'"):
        find_result_id("http://host", 7, record, Path("cookies"), fetch)


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
    from core.plan_file import load_plan_file

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

    _, _, _, classification = load_plan_file(path)
    plan = build_classification(classification)

    by_id = {rule.id: rule for rule in plan.rules}
    assert by_id["marked"].disposition() is None
    assert by_id["defaulted"].disposition() is True


# --------------------------------------------------------------------------
# Applying the plan against an instance
# --------------------------------------------------------------------------


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

    calls: list[tuple[str, str]] = []

    def fetch(url: str, *, method: str = "GET", **_: object) -> dict:
        calls.append((method, url))
        if "results/?" in url:
            return {"results": [{"result_id": 42, "run_id": 7, "parameters": []}]}
        if url.endswith("/classify/"):
            return {"issue_id": 3, "rule_id": 9}
        if url.endswith("/issues/3/"):
            return {"state": "open"}
        return {}

    manifest, path = _classify_manifest(tmp_path)
    apply_classification(manifest, path, "http://host", Path("jar"), fetch)

    classification = manifest["classification"]
    assert classification["issues"][0]["issueId"] == 3
    assert classification["rules"][0]["ruleId"] == 9
    assert classification["rules"][0]["classifiedResultIds"] == [42]
    assert ("POST", "http://host/api/v2/issues/3/close/") in calls


def test_apply_classification_is_idempotent(tmp_path: Path) -> None:
    """A re-run must neither duplicate rules nor claim work it did not do."""
    from core.classify_api import apply_classification

    calls: list[tuple[str, str]] = []

    def fetch(url: str, *, method: str = "GET", **_: object) -> dict:
        calls.append((method, url))
        if url.endswith("/issues/3/"):
            return {"state": "closed"}
        return {}

    manifest, path = _classify_manifest(tmp_path)
    manifest["classification"]["issues"][0]["issueId"] = 3
    manifest["classification"]["rules"][0]["ruleId"] = 9

    apply_classification(manifest, path, "http://host", Path("jar"), fetch)

    assert not [c for c in calls if c[1].endswith("/classify/")]
    assert not [c for c in calls if c[1].endswith("/close/")]


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
