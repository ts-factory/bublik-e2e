from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.bundle import FixtureSpec, apply_mix, generate_bundle, synchronize_json_logs
from core.common import read_json, write_json
from core.discovery import discover_fixtures
from core.manifest import collect_log_pages
from core.planning import MixValue


def _content(payload: dict, type_: str) -> dict:
    return next(item for item in payload["root"][0]["content"] if item["type"] == type_)


@pytest.mark.parametrize("fixture_name", ["basic", "dpdk-ethdev-ts", "net-drv-ts"])
def test_finalized_results_and_timestamps_are_synchronized_to_json_logs(
    tmp_path: Path, fixture_name: str
) -> None:
    fixture = discover_fixtures()[fixture_name]
    bundle_dir = tmp_path / fixture_name
    spec = FixtureSpec(
        id=f"{fixture_name}-run",
        fixture_name=fixture.name,
        fixture_id=f"e2e:{fixture_name}",
        project=fixture.project,
        conclusion="nok-warning",
        mix_name="test",
        run_date="2026-04-25",
        tags={"ordinal": "1"},
    )

    generate_bundle(fixture, spec, bundle_dir, pretty=True)
    apply_mix(
        bundle_dir,
        [MixValue("unexpectedFailed", 25, True)],
        "nok-warning",
        pretty=True,
    )

    bublik = read_json(bundle_dir / "bublik.json")
    root = bublik["iters"][0]
    failed = next(
        leaf
        for package in root["iters"]
        for leaf in ([package] if package["type"] == "test" else package["iters"])
        if leaf["obtained"]["result"]["status"] == "FAILED"
    )
    node_log = read_json(bundle_dir / "json" / f"node_id{failed['test_id']}.json")
    meta = _content(node_log, "te-log-meta")
    table = _content(node_log, "te-log-table")
    tree = read_json(bundle_dir / "json" / "tree.json")["tree"]

    assert meta["entity_model"]["result"] == "FAILED"
    assert meta["entity_model"]["error"] == "Unexpected test result(s)"
    assert meta["meta"]["start"] == failed["start_ts"].split(" ", 1)[1]
    assert meta["meta"]["end"] == failed["end_ts"].split(" ", 1)[1]
    assert meta["meta"]["verdicts"] == [
        {"verdict": "Generated unexpected result", "level": "ERROR"}
    ]
    assert tree[f"node_id{failed['test_id']}.json"]["has_error"] is True
    assert any(
        content.get("content") == "Obtained result is:\nFAILED"
        for row in table["data"]
        for content in row.get("log_content", [])
    )
    for row in table["data"]:
        timestamp = row.get("timestamp", {}).get("timestamp")
        if timestamp is not None:
            assert datetime.fromtimestamp(
                timestamp, tz=timezone.utc
            ).date().isoformat() in {
                "2026-04-25",
                "2026-04-26",
            }


def test_compromised_conclusion_emits_real_metadata(tmp_path: Path) -> None:
    fixture = discover_fixtures()["basic"]
    bundle_dir = tmp_path / "compromised"
    spec = FixtureSpec(
        id="compromised-run",
        fixture_name=fixture.name,
        fixture_id="e2e:compromised",
        project=fixture.project,
        conclusion="compromised",
        mix_name="all-ok",
        run_date="2026-04-25",
        tags={"ordinal": "1"},
    )

    generate_bundle(fixture, spec, bundle_dir, pretty=True)

    compromised = [
        meta
        for meta in read_json(bundle_dir / "meta_data.json")["metas"]
        if meta["name"] == "compromised"
    ]
    assert compromised == [{"name": "compromised", "value": ""}]


def test_numeric_node_log_timestamps_are_rebased(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    json_dir = bundle_dir / "json"
    node = {
        "test_id": 1,
        "name": "root",
        "type": "pkg",
        "path_str": "root",
        "start_ts": "2026.04.25 12:00:00.000",
        "end_ts": "2026.04.25 12:00:01.000",
        "start_ts_utc": 1000.0,
        "end_ts_utc": 1001.0,
        "obtained": {"result": {"status": "PASSED"}},
        "iters": [],
    }
    write_json(
        json_dir / "tree.json",
        {
            "main_package": "node_1_0.json",
            "tree": {"node_1_0.json": {"id": "node_1_0.json"}},
        },
        pretty=False,
    )
    write_json(
        json_dir / "node_1_0.json",
        {
            "root": [
                {
                    "content": [
                        {
                            "type": "te-log-table",
                            "data": [{"timestamp": 1000.5, "log_content": []}],
                        }
                    ]
                }
            ]
        },
        pretty=False,
    )

    synchronize_json_logs(
        bundle_dir,
        {"iters": [node]},
        pretty=False,
        timestamp_delta=500.0,
    )

    table = _content(read_json(json_dir / "node_1_0.json"), "te-log-table")
    assert table["data"][0]["timestamp"] == 1500.5


def test_paginated_node_logs_are_split_and_synchronized(tmp_path: Path) -> None:
    """A leaf that declares LOG_PAGES publishes one file per page, plus _all.

    Page one keeps the unsuffixed name and there is no ``_p1``: rgt only
    suffixes pages above one, so a ``_p1`` file would make ``?page=1`` look like
    a working link when against a published bundle it is a 404.
    """
    fixture = discover_fixtures()["basic"]
    bundle_dir = tmp_path / "paginated"
    spec = FixtureSpec(
        id="paginated-run",
        fixture_name=fixture.name,
        fixture_id="e2e:paginated",
        project=fixture.project,
        conclusion="nok-warning",
        mix_name="test",
        run_date="2026-04-25",
        tags={"ordinal": "1"},
    )

    generate_bundle(fixture, spec, bundle_dir, pretty=True)
    apply_mix(
        bundle_dir,
        [MixValue("unexpectedFailed", 25, True)],
        "nok-warning",
        pretty=True,
    )

    json_dir = bundle_dir / "json"
    entries = collect_log_pages(bundle_dir)
    by_pages = {entry["pagesCount"] for entry in entries}
    assert by_pages == {1, 2, 3}

    long_entry = next(entry for entry in entries if entry["pagesCount"] == 1)
    assert long_entry["rowCount"] >= 500

    for entry in entries:
        leaf = next(
            leaf
            for leaf in _leaves(read_json(bundle_dir / "bublik.json")["iters"][0])
            if leaf["path_str"] == entry["pathStr"] and leaf["tin"] == entry["tin"]
        )
        test_id = leaf["test_id"]
        page_one = read_json(json_dir / f"node_id{test_id}.json")
        pages = entry["pagesCount"]

        if pages == 1:
            assert "pagination" not in page_one["root"][0]
            assert not (json_dir / f"node_id{test_id}_all.json").exists()
            continue

        assert not (json_dir / f"node_id{test_id}_p1.json").exists()
        assert page_one["root"][0]["pagination"] == {
            "cur_page": 1,
            "pages_count": pages,
        }

        rows = list(_content(page_one, "te-log-table")["data"])
        for number in range(2, pages + 1):
            payload = read_json(json_dir / f"node_id{test_id}_p{number}.json")
            assert payload["root"][0]["pagination"] == {
                "cur_page": number,
                "pages_count": pages,
            }
            # Every page is a complete document: drop the meta and the log
            # header disappears the moment the reader pages forward.
            assert _content(payload, "te-log-meta")
            rows.extend(_content(payload, "te-log-table")["data"])

        combined = read_json(json_dir / f"node_id{test_id}_all.json")
        assert combined["root"][0]["pagination"] == {"cur_page": 0, "pages_count": 0}
        all_rows = _content(combined, "te-log-table")["data"]

        assert rows == all_rows
        # Continuous numbering is what lets a bookmarked line resolve on exactly
        # one page instead of colliding with a row of the same number elsewhere.
        assert [row["line_number"] for row in rows] == list(range(1, len(rows) + 1))
        assert len(rows) == entry["rowCount"]

        # The result row is the last row of the node, so it only exists on the
        # final page and in _all -- both of which the mix has to reach.
        status = leaf["obtained"]["result"]["status"]
        last_page = read_json(json_dir / f"node_id{test_id}_p{pages}.json")
        for payload in (last_page, combined):
            assert any(
                content.get("content") == f"Obtained result is:\n{status}"
                for row in _content(payload, "te-log-table")["data"]
                for content in row.get("log_content", [])
            )


def _leaves(node: dict) -> list[dict]:
    children = node.get("iters") or []
    if node.get("type") == "test" and not children:
        return [node]
    return [leaf for child in children for leaf in _leaves(child)]
