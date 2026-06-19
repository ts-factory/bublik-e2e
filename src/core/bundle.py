"""Generate a fixture bundle, stamp metadata, and apply the result mix."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
import math
from pathlib import Path
from typing import Any

from core.common import CliError, read_json, write_json
from core.constants import (
    NOK_BORDERS,
    RESULT_TYPES,
    RUN_COMPLETE_FILE,
    RUN_STATUS_BY_CONCLUSION,
    UNFINISHED_CONCLUSIONS,
)
from core.fixture_api import FixtureProvider
from core.planning import MixValue, PlannedRun, parse_date, parse_mix_key
from core.settings import DEFAULT_TIMEZONE


@dataclass
class FixtureSpec:
    id: str
    fixture_name: str
    fixture_id: str
    project: str
    conclusion: str
    mix_name: str
    run_date: str
    metas: dict[str, str] = field(default_factory=dict)
    tags: dict[str, str] = field(default_factory=dict)


def upsert_meta(
    metas: list[dict[str, Any]], name: str, value: str, type_: str | None = None
) -> None:
    replacement = {"name": name, "value": value}
    if type_:
        replacement["type"] = type_
    metas[:] = [meta for meta in metas if meta.get("name") != name]
    metas.append(replacement)


def get_meta_value(metas: list[dict[str, Any]], name: str) -> str | None:
    for meta in metas:
        if meta.get("name") == name:
            value = meta.get("value")
            return value if isinstance(value, str) else None
    return None


def iso_for_day(run_date: str, ordinal: int) -> str:
    day = parse_date(run_date).date()
    ts = datetime.combine(day, time(12, 0, 0), DEFAULT_TIMEZONE)
    ts += timedelta(seconds=ordinal * 17, milliseconds=ordinal)
    return ts.isoformat(timespec="milliseconds")


def parse_bublik_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y.%m.%d %H:%M:%S.%f")


def format_bublik_timestamp(value: datetime) -> str:
    return value.strftime("%Y.%m.%d %H:%M:%S.%f")[:-3]


def quantize_ms(value: datetime) -> datetime:
    """Drop sub-millisecond precision.

    Bublik stores node times from the float ``*_ts_utc`` (microsecond precision)
    but the ``*_ts`` string is millisecond-truncated. Quantizing the instant
    before producing both keeps the two representations identical, so no
    sub-millisecond gap survives to confuse the whole-day-only
    ``fix_result_timestamps`` repair on import.
    """
    return value.replace(microsecond=(value.microsecond // 1000) * 1000)


def add_utc_timestamps(node: dict[str, Any], offset: timedelta) -> None:
    for key in ("start_ts", "end_ts"):
        value = node.get(key)
        if not value or f"{key}_utc" in node:
            continue
        local = quantize_ms(datetime.strptime(value, "%Y.%m.%d %H:%M:%S.%f"))
        node[f"{key}_utc"] = local.replace(tzinfo=timezone(offset)).timestamp()

    for child in node.get("iters") or []:
        add_utc_timestamps(child, offset)


def rebase_timestamps(
    node: dict[str, Any],
    *,
    original_root_start: datetime,
    new_root_start: datetime,
    tz_offset: timedelta,
) -> None:
    for key in ("start_ts", "end_ts"):
        value = node.get(key)
        if not value:
            continue
        rebased = quantize_ms(
            new_root_start + (parse_bublik_timestamp(value) - original_root_start)
        )
        node[key] = format_bublik_timestamp(rebased)
        node[f"{key}_utc"] = rebased.replace(tzinfo=timezone(tz_offset)).timestamp()

    for child in node.get("iters") or []:
        rebase_timestamps(
            child,
            original_root_start=original_root_start,
            new_root_start=new_root_start,
            tz_offset=tz_offset,
        )


def apply_run_profile(
    meta_items: list[dict[str, Any]],
    bublik_tags: dict[str, Any],
    profile: Any | None,
) -> None:
    if profile is None:
        return
    for name, value in getattr(profile, "metas", {}).items():
        upsert_meta(meta_items, name, value)
    bublik_tags.clear()
    bublik_tags.update(getattr(profile, "tags", {}))
    bublik_tags["source_profile"] = getattr(profile, "name", "real-world")


def patch_bundle(
    output_dir: Path,
    *,
    fixture: FixtureProvider,
    spec: FixtureSpec,
    pretty: bool,
) -> None:
    meta_path = output_dir / "meta_data.json"
    bublik_path = output_dir / "bublik.json"
    meta_data = read_json(meta_path)
    bublik_data = read_json(bublik_path)

    meta_items = meta_data.setdefault("metas", [])
    # iso_for_day only chooses the target date/offset the run is rebased onto.
    target_start = iso_for_day(spec.run_date, int(spec.tags.get("ordinal", "0")))
    start_datetime = datetime.fromisoformat(target_start)
    start_offset = start_datetime.utcoffset() or timedelta()
    start_timestamp = target_start
    finish_timestamp = target_start
    roots = bublik_data.get("iters") or []
    if roots:
        root = roots[0]
        original_root_start = parse_bublik_timestamp(root["start_ts"])
        new_root_start = start_datetime.replace(tzinfo=None)
        rebase_timestamps(
            root,
            original_root_start=original_root_start,
            new_root_start=new_root_start,
            tz_offset=start_offset,
        )
        bublik_data["start_ts"] = root.get("start_ts")
        bublik_data["end_ts"] = root.get("end_ts")
        # Derive run boundaries from the rebased root's own *_ts_utc — the exact
        # float Bublik stores for the node — so run.start == first node start and
        # run.finish == last node finish, with no millisecond-truncation gap.
        run_tz = timezone(start_offset)
        start_timestamp = datetime.fromtimestamp(root["start_ts_utc"], tz=run_tz).isoformat()
        finish_timestamp = datetime.fromtimestamp(root["end_ts_utc"], tz=run_tz).isoformat()
    upsert_meta(meta_items, "PROJECT", spec.project)
    upsert_meta(meta_items, "RUN_STATUS", spec.metas.get("RUN_STATUS", "DONE"))
    upsert_meta(meta_items, "E2E_RUN_ID", spec.fixture_id, "label")
    upsert_meta(meta_items, "CFG", spec.id)
    upsert_meta(meta_items, "START_TIMESTAMP", start_timestamp, "timestamp")
    if spec.conclusion not in UNFINISHED_CONCLUSIONS:
        upsert_meta(meta_items, "FINISH_TIMESTAMP", finish_timestamp, "timestamp")
    else:
        # Still-running runs have no finish; drop any inherited timestamp.
        meta_items[:] = [m for m in meta_items if m.get("name") != "FINISH_TIMESTAMP"]
    upsert_meta(meta_items, "CAMPAIGN_DATE", spec.run_date)
    for key, value in spec.metas.items():
        if key not in {"RUN_STATUS"}:
            upsert_meta(meta_items, key, value)

    bublik_tags = bublik_data.setdefault("tags", {})
    profile = None
    profile_for = getattr(fixture, "profile_for", None)
    if callable(profile_for):
        profile = profile_for(spec.conclusion, int(spec.tags.get("ordinal", "1")))
    apply_run_profile(meta_items, bublik_tags, profile)
    bublik_tags.update(
        {
            "fixture_id": spec.fixture_id,
            "fixture": spec.fixture_name,
            "conclusion": spec.conclusion,
            "mix": spec.mix_name,
        }
    )
    bublik_tags.update(spec.tags)
    for root in bublik_data.get("iters", []):
        add_utc_timestamps(root, start_offset)

    write_json(meta_path, meta_data, pretty)
    write_json(bublik_path, bublik_data, pretty)


def generate_bundle(
    fixture: FixtureProvider,
    spec: FixtureSpec,
    output_dir: Path,
    pretty: bool,
) -> Path:
    try:
        fixture.generate(output_dir, pretty)
    except Exception as exc:
        raise CliError(f"fixture {fixture.name!r} generation failed: {exc}") from exc
    for required in ("meta_data.json", "bublik.json"):
        if not (output_dir / required).is_file():
            raise CliError(f"fixture {fixture.name!r} did not create {required}")
    patch_bundle(output_dir, fixture=fixture, spec=spec, pretty=pretty)
    # Bublik only stores a run's finish (Start/Finish/Duration in the UI) when it can
    # fetch this marker at the run URL; write it for finished runs only.
    if spec.conclusion not in UNFINISHED_CONCLUSIONS:
        (output_dir / RUN_COMPLETE_FILE).write_text("")
    return output_dir


def collect_leaf_tests(bublik: dict[str, Any]) -> list[dict[str, Any]]:
    root = bublik["iters"][0]
    leaves: list[dict[str, Any]] = []

    def visit(node: dict[str, Any]) -> None:
        children = node.get("iters") or []
        if node.get("type") == "test" and not children:
            leaves.append(node)
        for child in children:
            visit(child)

    visit(root)
    return leaves


def leaf_tests(bundle_dir: Path) -> list[dict[str, Any]]:
    return collect_leaf_tests(read_json(bundle_dir / "bublik.json"))


def set_leaf_result(node: dict[str, Any], status: str, unexpected: bool) -> None:
    expected_status = "PASSED" if unexpected else status
    if unexpected and status == "PASSED":
        expected_status = "FAILED"
    node.setdefault("obtained", {}).setdefault("result", {})["status"] = status
    result = node.setdefault("expected", {}).setdefault("results", [{}])
    if not result:
        result.append({})
    result[0]["status"] = expected_status
    if unexpected:
        node["obtained"]["result"]["verdicts"] = ["Generated unexpected result"]
        node["err"] = "Unexpected test result(s)"
    else:
        node["obtained"]["result"]["verdicts"] = []
        node["err"] = ""


def recompute_package_statuses(node: dict[str, Any]) -> str:
    children = node.get("iters") or []
    if not children:
        return node.get("obtained", {}).get("result", {}).get("status", "INCOMPLETE")

    child_statuses = [recompute_package_statuses(child) for child in children]
    if any(
        status in {"FAILED", "KILLED", "CORED", "FAKED", "INCOMPLETE"}
        for status in child_statuses
    ):
        status = "FAILED"
    elif child_statuses and all(status == "SKIPPED" for status in child_statuses):
        status = "SKIPPED"
    else:
        status = "PASSED"

    node.setdefault("obtained", {}).setdefault("result", {})["status"] = status
    node["err"] = "" if status == "PASSED" else node.get("err", "")
    return status


def is_unexpected_leaf(node: dict[str, Any]) -> bool:
    obtained = node.get("obtained", {}).get("result", {}).get("status", "INCOMPLETE")
    expected_values = [
        item.get("status")
        for item in node.get("expected", {}).get("results", [])
        if item.get("status")
    ]
    expected_status = expected_values[0] if expected_values else "PASSED"
    return obtained != expected_status


def apply_mix(
    bundle_dir: Path, mix: list[MixValue], conclusion: str, pretty: bool
) -> None:
    bublik_path = bundle_dir / "bublik.json"
    bublik = read_json(bublik_path)
    leaves = collect_leaf_tests(bublik)
    total = len(leaves)
    if total == 0:
        raise CliError(f"fixture {bundle_dir} has no leaf tests")

    if conclusion == "ok" and not mix:
        mix = []
    if conclusion == "nok-warning" and not mix:
        mix = [MixValue("unexpectedFailed", NOK_BORDERS[0] + 1, True)]
    if conclusion == "nok-error" and not mix:
        mix = [MixValue("unexpectedFailed", NOK_BORDERS[1], True)]

    for leaf in leaves:
        set_leaf_result(leaf, "PASSED", False)

    assignments: list[tuple[str, bool]] = []
    for item in mix:
        prop, type_name = parse_mix_key(item.key)
        count = (
            math.ceil(total * item.value / 100)
            if item.is_percent
            else int(item.value)
        )
        if count < 0:
            raise CliError(f"invalid negative count in mix {item.key}")
        status = RESULT_TYPES[type_name]
        unexpected = prop == "unexpected"
        if prop == "notRun":
            status = "INCOMPLETE" if type_name == "incomplete" else status
        assignments.extend([(status, unexpected)] * count)

    if len(assignments) > total:
        raise CliError(f"mix uses more results than fixture has: {bundle_dir}")

    # Scatter the assignments across the whole leaf list using a coprime
    # golden-ratio stride so every package gets a representative share, instead
    # of clustering all non-passing results in the first few packages.
    stride = max(1, int(total * 0.6180339887))
    while total > 1 and math.gcd(stride, total) != 1:
        stride += 1
    for offset, (status, unexpected) in enumerate(assignments):
        set_leaf_result(leaves[(offset * stride) % total], status, unexpected)

    for root in bublik.get("iters", []):
        recompute_package_statuses(root)

    unexpected_count = sum(1 for leaf in leaves if is_unexpected_leaf(leaf))
    unexpected_percent = round(unexpected_count / total * 100) if total else 0
    if conclusion == "nok-warning" and not (
        NOK_BORDERS[0] < unexpected_percent < NOK_BORDERS[1]
    ):
        raise CliError(
            f"nok-warning mix resolved to {unexpected_percent}% unexpected; "
            f"expected between {NOK_BORDERS[0]} and {NOK_BORDERS[1]}"
        )
    if conclusion == "nok-error" and unexpected_percent < NOK_BORDERS[1]:
        raise CliError(
            f"nok-error mix resolved to {unexpected_percent}% unexpected; "
            f"expected at least {NOK_BORDERS[1]}"
        )

    write_json(bublik_path, bublik, pretty)


def spec_from_plan(plan: PlannedRun) -> FixtureSpec:
    status = RUN_STATUS_BY_CONCLUSION[plan.conclusion]
    return FixtureSpec(
        id=plan.id,
        fixture_name=plan.fixture.name,
        fixture_id=f"{plan.fixture.fixture_id_prefix}:{plan.id}",
        project=plan.fixture.project,
        conclusion=plan.conclusion,
        mix_name=plan.mix_name,
        run_date=plan.run_date,
        metas={"RUN_STATUS": status},
        tags={"ordinal": str(plan.ordinal)},
    )
