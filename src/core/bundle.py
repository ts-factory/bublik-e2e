"""Generate a fixture bundle, stamp metadata, and apply the result mix."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
import math
from pathlib import Path
import re
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


def _status_level(status: str) -> str:
    if status == "PASSED":
        return "RING"
    if status in {"FAILED", "KILLED", "CORED"}:
        return "ERROR"
    if status in {"SKIPPED", "FAKED", "INCOMPLETE"}:
        return "WARN"
    return "INFO"


def _node_status(node: dict[str, Any]) -> str:
    return node.get("obtained", {}).get("result", {}).get("status", "INCOMPLETE")


def _duration_str(node: dict[str, Any]) -> str:
    total_ms = max(
        0, int((node.get("end_ts_utc", 0) - node.get("start_ts_utc", 0)) * 1000)
    )
    total_seconds, milliseconds = divmod(total_ms, 1000)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes}:{seconds}.{milliseconds:03}"


def _update_entity_model(model: dict[str, Any], node: dict[str, Any]) -> None:
    status = _node_status(node)
    model.update(
        {
            "id": str(node["test_id"]),
            "name": node["name"],
            "entity": "Package" if node.get("type") == "pkg" else "Test",
            "result": status,
        }
    )
    extended = model.setdefault("extended_properties", {})
    extended["path"] = node.get("path_str", "")
    if node.get("type") == "test":
        extended["tin"] = str(node.get("tin", -1))
        extended["hash"] = node.get("hash", "")
    else:
        extended.pop("tin", None)
        extended.pop("hash", None)
    if node.get("err"):
        model["error"] = node["err"]
    else:
        model.pop("error", None)


def _update_node_meta(meta: dict[str, Any], node: dict[str, Any]) -> None:
    meta.update(
        {
            "start": node["start_ts"].split(" ", 1)[1],
            "end": node["end_ts"].split(" ", 1)[1],
            "duration": _duration_str(node),
        }
    )
    result = node.get("obtained", {}).get("result", {})
    level = _status_level(_node_status(node))
    for source, target, item_key in (
        (result.get("verdicts") or [], "verdicts", "verdict"),
        (result.get("artifacts") or [], "artifacts", "artifact"),
    ):
        if source:
            meta[target] = [{item_key: item, "level": level} for item in source]
        else:
            meta.pop(target, None)


def _text_row_content(row: dict[str, Any]) -> tuple[dict[str, Any], str] | None:
    for content in row.get("log_content") or []:
        if content.get("type") == "te-log-table-content-text" and isinstance(
            content.get("content"), str
        ):
            return content, content["content"]
    return None


def synchronize_json_logs(
    bundle_dir: Path,
    bublik: dict[str, Any],
    pretty: bool,
    *,
    timestamp_delta: float = 0,
    tz_offset: timedelta = timedelta(),
) -> None:
    """Synchronize provider-generated JSON logs with finalized run data.

    Existing provider-specific table rows are retained. Only node/tree metadata,
    known result rows, and timestamps changed by rebasing are updated.
    """
    json_dir = bundle_dir / "json"
    tree_path = json_dir / "tree.json"
    roots = bublik.get("iters") or []
    if not tree_path.is_file() or not roots:
        return

    root = roots[0]
    nodes_by_id: dict[int, dict[str, Any]] = {}

    def collect(node: dict[str, Any]) -> None:
        nodes_by_id[node["test_id"]] = node
        for child in node.get("iters") or []:
            collect(child)

    collect(root)
    tree_data = read_json(tree_path)
    main_package = tree_data.get("main_package", "node_1_0.json")

    def node_for_file(file_name: str) -> dict[str, Any] | None:
        if file_name == main_package or file_name == "node_1_0.json":
            return root
        # A paginating node publishes node_id<N>.json (page one), node_id<N>_p<K>
        # for the pages above it, and node_id<N>_all.json. All of them describe
        # the same node and all of them need the same finalized data.
        match = re.fullmatch(r"node_id(\d+)(?:_p\d+|_all)?\.json", file_name)
        return nodes_by_id.get(int(match.group(1))) if match else None

    tree = tree_data.get("tree", {})
    for file_name, entry in tree.items():
        node = node_for_file(file_name)
        if node is None:
            continue
        status = _node_status(node)
        entry.update(
            {
                "id": file_name,
                "name": node["name"],
                "has_error": status not in {"PASSED", "SKIPPED"},
                "skipped": status == "SKIPPED",
                "entity": node["type"],
            }
        )
    write_json(tree_path, tree_data, pretty)

    files = set(tree)
    if (json_dir / "node_id1.json").is_file():
        files.add("node_id1.json")
    # tree.json lists page-one files only; the rest have to be discovered.
    files.update(path.name for path in json_dir.glob("node_id*_p*.json"))
    files.update(path.name for path in json_dir.glob("node_id*_all.json"))
    run_timezone = timezone(tz_offset)
    for file_name in files:
        node = node_for_file(file_name)
        path = json_dir / file_name
        if node is None or not path.is_file():
            continue
        payload = read_json(path)
        content_items = (payload.get("root") or [{}])[0].get("content") or []
        children = node.get("iters") or []
        for item in content_items:
            if item.get("type") == "te-log-meta":
                _update_entity_model(item.setdefault("entity_model", {}), node)
                _update_node_meta(item.setdefault("meta", {}), node)
            elif item.get("type") == "te-log-entity-list":
                for index, model in enumerate(item.get("items") or []):
                    try:
                        child = nodes_by_id.get(int(model.get("id", "")))
                    except (TypeError, ValueError):
                        child = None
                    if child is None and index < len(children):
                        child = children[index]
                    if child is not None:
                        _update_entity_model(model, child)
            elif item.get("type") == "te-log-table":
                for row in item.get("data") or []:
                    timestamp = row.get("timestamp")
                    if timestamp_delta:
                        if isinstance(timestamp, dict) and isinstance(
                            timestamp.get("timestamp"), (int, float)
                        ):
                            shifted = timestamp["timestamp"] + timestamp_delta
                            timestamp["timestamp"] = shifted
                            timestamp["formatted"] = datetime.fromtimestamp(
                                shifted, tz=run_timezone
                            ).strftime("%H:%M:%S.%f")[:-3]
                        elif isinstance(timestamp, (int, float)):
                            row["timestamp"] = timestamp + timestamp_delta

                    text_item = _text_row_content(row)
                    if text_item is None:
                        continue
                    text, value = text_item
                    if value.startswith("Obtained result is:\n"):
                        text["content"] = f"Obtained result is:\n{_node_status(node)}"
                        row["level"] = _status_level(_node_status(node))
                    elif value.startswith("RESULT status="):
                        expected = (node.get("expected", {}).get("results") or [{}])[
                            0
                        ].get("status", "PASSED")
                        replacement = (
                            f"RESULT status={_node_status(node)} expected={expected}"
                        )
                        if node.get("err"):
                            replacement += f" err={node['err']}"
                        text["content"] = replacement
                        row["level"] = _status_level(_node_status(node))
                    elif children and row.get("user_name") == "Step":
                        # A node with children emits exactly one row per child,
                        # numbered from one. Deriving the child from the row's
                        # own line number rather than from a running counter
                        # keeps this correct when the rows are split across
                        # pages, where each file starts part-way through.
                        child_index = int(row.get("line_number", 0)) - 1
                        if 0 <= child_index < len(children):
                            row["level"] = _status_level(
                                _node_status(children[child_index])
                            )
        write_json(path, payload, pretty)


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
    timestamp_delta = 0.0
    if roots:
        root = roots[0]
        original_root_start = parse_bublik_timestamp(root["start_ts"])
        original_root_start_utc = root.get("start_ts_utc")
        if not isinstance(original_root_start_utc, (int, float)):
            local_timezone = datetime.now().astimezone().tzinfo or timezone.utc
            original_root_start_utc = original_root_start.replace(
                tzinfo=local_timezone
            ).timestamp()
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
        start_timestamp = datetime.fromtimestamp(
            root["start_ts_utc"], tz=run_tz
        ).isoformat()
        finish_timestamp = datetime.fromtimestamp(
            root["end_ts_utc"], tz=run_tz
        ).isoformat()
        timestamp_delta = root["start_ts_utc"] - original_root_start_utc
    upsert_meta(meta_items, "PROJECT", spec.project)
    upsert_meta(meta_items, "RUN_STATUS", spec.metas.get("RUN_STATUS", "DONE"))
    upsert_meta(meta_items, "E2E_RUN_ID", spec.fixture_id)
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
    if spec.conclusion == "compromised":
        upsert_meta(meta_items, "compromised", "")
    else:
        meta_items[:] = [m for m in meta_items if m.get("name") != "compromised"]
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
    synchronize_json_logs(
        output_dir,
        bublik_data,
        pretty,
        timestamp_delta=timestamp_delta,
        tz_offset=start_offset,
    )


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
            math.ceil(total * item.value / 100) if item.is_percent else int(item.value)
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
    start_timestamp = read_json(bundle_dir / "meta_data.json").get("metas", [])
    start_value = get_meta_value(start_timestamp, "START_TIMESTAMP")
    offset = (
        datetime.fromisoformat(start_value).utcoffset() if start_value else timedelta()
    )
    synchronize_json_logs(bundle_dir, bublik, pretty, tz_offset=offset or timedelta())


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
