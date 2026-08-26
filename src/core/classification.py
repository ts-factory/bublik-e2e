"""Classification fixtures: pinned leaves, issues and the rules over them.

The classification feature stamps a test result when an active ``IssueRule``
matches it, and applies those rules automatically on every import. Testing that
end to end needs three things the plain fixture campaign cannot express:

1. **Stable, addressable failures.** A mix says "22% unexpectedFailed" and
   scatters them; it cannot say "``send_receive`` fails". A :class:`Pin` names a
   leaf by fixture and test and forces its status and verdicts.

2. **Distinguishable verdicts.** Every unexpected leaf otherwise carries the same
   generated verdict text, so a rule matching on verdicts cannot be shown to
   discriminate. A pin authors the verdict strings.

3. **Rules of different shapes.** A rule matches on test (always), and optionally
   on parameters, verdicts and run tags. :class:`RuleSpec` picks which dimensions
   to keep, so the suite can cover each shape and each disposition.

Ordering is what makes the cross-run assertion meaningful: rules are created
against an already-imported run, and a *later* import is what proves they apply
to runs that did not exist when the rule was written. The plan therefore marks
which runs are seeded first (``api``) and which are held back (``+ui``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.common import CliError
from core.constants import RESULT_TYPES, RUN_STATUS_BY_CONCLUSION

# Matcher dimensions a rule may keep beyond the test itself. The backend always
# requires the test, matches ``parameters`` as a dict-subset and ``verdicts`` as a
# set-subset, and treats ``tags`` as a run-level gate. An empty dimension means
# "do not apply this criterion", which is how a test-only rule is expressed.
MATCH_DIMENSIONS = ("parameters", "verdicts", "tags")

# Mirrors IssueCategory in bublik/data/models/issue.py.
CATEGORIES = (
    "product-defect",
    "test-bug",
    "env",
    "known-issue",
    "flaky",
    "to-investigate",
)

# Categories whose default disposition suppresses the failure, mirroring
# _EXPECTED_BY_CATEGORY in the same module. A rule may still override it.
EXPECTED_BY_CATEGORY = {
    "known-issue": True,
    "env": True,
    "test-bug": True,
    "flaky": True,
    "product-defect": False,
    "to-investigate": False,
}

SCOPES = ("future", "oneoff")


def default_expected_for(category: str) -> bool:
    return EXPECTED_BY_CATEGORY[category]


@dataclass(frozen=True)
class Pin:
    """A leaf forced to a known status and verdict set in every matching run.

    ``iterations`` selects leaves by ``tin`` (the index of the iteration within
    its test family); empty means every iteration of the test. ``conclusions``
    limits the pin to runs of those conclusions; empty means every run of the
    fixture. Pinning an unexpected result into an ``ok`` run would change that
    run's conclusion, so a pin that sets ``unexpected`` should name the
    conclusions it belongs in.
    """

    id: str
    fixture: str
    test: str
    status: str = "FAILED"
    unexpected: bool = True
    verdicts: tuple[str, ...] = ()
    iterations: tuple[int, ...] = ()
    conclusions: tuple[str, ...] = ()

    def applies_to(self, fixture: str, conclusion: str) -> bool:
        if self.fixture != fixture:
            return False
        return not self.conclusions or conclusion in self.conclusions

    def selects(self, tin: int) -> bool:
        return not self.iterations or tin in self.iterations


@dataclass(frozen=True)
class IssueSpec:
    """An issue to create. ``key`` is a ``ref://TRACKER/KEY`` external reference."""

    id: str
    title: str
    description: str | None = None
    key: str | None = None
    #: Close the issue once its rules exist. Closing deactivates the issue's
    #: rules and lifts suppression, which is the "stale classification" state.
    close: bool = False


@dataclass(frozen=True)
class RuleSpec:
    """A rule to create, by classifying one result of ``pin``.

    Rules are created through ``POST /results/{id}/classify/`` rather than by
    constructing them directly: that is the endpoint the UI uses, it resolves the
    test itself, and it captures the matcher from the result. ``match`` narrows
    that captured matcher down to the dimensions under test.
    """

    id: str
    issue: str
    pin: str
    category: str = "known-issue"
    #: Resolved disposition, tri-state. ``True`` suppresses the failure,
    #: ``False`` leaves it counting, ``None`` marks it without deciding. An
    #: omitted ``expected`` is defaulted from the category at build time, so an
    #: explicit ``null`` survives as a real "marker only" rule.
    expected: bool | None = None
    scope: str = "future"
    match: tuple[str, ...] = ()

    def disposition(self) -> bool | None:
        return self.expected


@dataclass
class ClassificationPlan:
    """The classification section of a plan, validated and cross-referenced."""

    pins: tuple[Pin, ...] = ()
    issues: tuple[IssueSpec, ...] = ()
    rules: tuple[RuleSpec, ...] = ()

    def pin_by_id(self, pin_id: str) -> Pin:
        for pin in self.pins:
            if pin.id == pin_id:
                return pin
        raise CliError(f"unknown pin {pin_id!r}")

    def issue_by_id(self, issue_id: str) -> IssueSpec:
        for issue in self.issues:
            if issue.id == issue_id:
                return issue
        raise CliError(f"unknown issue {issue_id!r}")

    def pins_for(self, fixture: str, conclusion: str) -> tuple[Pin, ...]:
        return tuple(pin for pin in self.pins if pin.applies_to(fixture, conclusion))

    def is_empty(self) -> bool:
        return not (self.pins or self.issues or self.rules)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CliError(message)


def _unique_ids(items: list[dict[str, Any]], kind: str) -> None:
    seen: set[str] = set()
    for item in items:
        item_id = item.get("id")
        _require(bool(item_id), f"every classification {kind} needs an id")
        _require(
            item_id not in seen,
            f"duplicate classification {kind} id {item_id!r}",
        )
        seen.add(str(item_id))


def build_classification(raw: dict[str, Any] | None) -> ClassificationPlan:
    """Validate the plan's ``classification:`` mapping into a domain object.

    The shape is already checked by the plan model; this resolves the
    cross-references between rules, issues and pins, which the shape cannot.
    """
    if not raw:
        return ClassificationPlan()

    raw_pins = list(raw.get("pins") or [])
    raw_issues = list(raw.get("issues") or [])
    raw_rules = list(raw.get("rules") or [])
    _unique_ids(raw_pins, "pin")
    _unique_ids(raw_issues, "issue")
    _unique_ids(raw_rules, "rule")

    pins: list[Pin] = []
    for item in raw_pins:
        status = str(item.get("status", "FAILED")).upper()
        _require(
            status in RESULT_TYPES.values(),
            f"pin {item['id']!r} has unknown status {status!r}; "
            f"expected one of: {', '.join(sorted(set(RESULT_TYPES.values())))}",
        )
        for conclusion in item.get("conclusions") or []:
            _require(
                conclusion in RUN_STATUS_BY_CONCLUSION,
                f"pin {item['id']!r} names unknown conclusion {conclusion!r}",
            )
        verdicts = tuple(str(v) for v in item.get("verdicts") or ())
        _require(
            len(set(verdicts)) == len(verdicts),
            f"pin {item['id']!r} repeats a verdict",
        )
        pins.append(
            Pin(
                id=str(item["id"]),
                fixture=str(item["fixture"]),
                test=str(item["test"]),
                status=status,
                unexpected=bool(item.get("unexpected", True)),
                verdicts=verdicts,
                iterations=tuple(int(i) for i in item.get("iterations") or ()),
                conclusions=tuple(item.get("conclusions") or ()),
            )
        )

    issues = tuple(
        IssueSpec(
            id=str(item["id"]),
            title=str(item["title"]),
            description=item.get("description"),
            key=item.get("key"),
            close=bool(item.get("close", False)),
        )
        for item in raw_issues
    )

    plan = ClassificationPlan(pins=tuple(pins), issues=issues)

    rules: list[RuleSpec] = []
    for item in raw_rules:
        category = str(item.get("category", "known-issue"))
        _require(
            category in CATEGORIES,
            f"rule {item['id']!r} has unknown category {category!r}; "
            f"expected one of: {', '.join(CATEGORIES)}",
        )
        scope = str(item.get("scope", "future"))
        _require(
            scope in SCOPES,
            f"rule {item['id']!r} has unknown scope {scope!r}; "
            f"expected one of: {', '.join(SCOPES)}",
        )
        match = tuple(str(m) for m in item.get("match") or ())
        for dimension in match:
            _require(
                dimension in MATCH_DIMENSIONS,
                f"rule {item['id']!r} matches on unknown dimension "
                f"{dimension!r}; expected any of: {', '.join(MATCH_DIMENSIONS)}",
            )
        _require(
            len(set(match)) == len(match),
            f"rule {item['id']!r} repeats a match dimension",
        )
        # Cross-references: fail here, with the rule's own id, rather than
        # halfway through an import against a live instance.
        plan.issue_by_id(str(item["issue"]))
        pin = plan.pin_by_id(str(item["pin"]))
        if "verdicts" in match:
            _require(
                bool(pin.verdicts),
                f"rule {item['id']!r} matches on verdicts but its pin "
                f"{pin.id!r} authors none",
            )
        # Present-but-null means "marker only"; absent means "use the category
        # default". model_dump(exclude_unset=True) upstream is what keeps the two
        # apart, so test membership rather than truthiness.
        expected = (
            item["expected"] if "expected" in item else default_expected_for(category)
        )
        rules.append(
            RuleSpec(
                id=str(item["id"]),
                issue=str(item["issue"]),
                pin=str(item["pin"]),
                category=category,
                expected=expected,
                scope=scope,
                match=match,
            )
        )

    plan.rules = tuple(rules)
    return plan


def classification_manifest(
    plan: ClassificationPlan, bundles: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Render the classification section of the manifest, or ``None`` when unused.

    ``issueId``/``ruleId`` are filled in later by ``--setup-classification``; they
    stay null when the triage is left to the UI, which is what tells the suite
    whether to create the issues itself or assert against existing ones.

    ``seededIn``/``appliesTo`` split the bundles by import wave. A rule is written
    against a run in ``seededIn`` (imported through the API before the rule
    exists) and is expected to stamp the runs in ``appliesTo`` (imported later,
    through the UI), which is the assertion the whole fixture exists to support.
    """
    if plan.is_empty():
        return None

    pinned_bundles: dict[str, list[dict[str, Any]]] = {}
    for bundle in bundles:
        for record in bundle.get("pinnedResults") or []:
            pinned_bundles.setdefault(record["pin"], []).append(bundle)

    def wave(pin_id: str, via: str) -> list[str]:
        # A pin usually forces several leaves per bundle, so dedupe: these list
        # the runs the pin reached, not the leaves it touched.
        return sorted(
            {
                bundle["id"]
                for bundle in pinned_bundles.get(pin_id, [])
                if bundle.get("importVia", "api") == via
            }
        )

    return {
        "pins": [
            {
                "id": pin.id,
                "fixture": pin.fixture,
                "test": pin.test,
                "status": pin.status,
                "unexpected": pin.unexpected,
                "verdicts": list(pin.verdicts),
                "iterations": list(pin.iterations),
                "conclusions": list(pin.conclusions),
                "seededIn": wave(pin.id, "api"),
                "appliesTo": wave(pin.id, "ui"),
            }
            for pin in plan.pins
        ],
        "issues": [
            {
                "id": issue.id,
                "title": issue.title,
                "description": issue.description,
                "key": issue.key,
                "close": issue.close,
                "issueId": None,
            }
            for issue in plan.issues
        ],
        "rules": [
            {
                "id": rule.id,
                "issue": rule.issue,
                "pin": rule.pin,
                "category": rule.category,
                "expected": rule.disposition(),
                "scope": rule.scope,
                "match": list(rule.match),
                "ruleId": None,
            }
            for rule in plan.rules
        ],
    }
