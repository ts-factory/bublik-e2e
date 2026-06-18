from __future__ import annotations

import argparse

import pytest

from core.common import CliError
from core.planning import (
    build_mixes,
    parse_date,
    parse_date_range,
    parse_day_entry,
    parse_mix_entry,
)


def test_parse_date_range_inclusive() -> None:
    assert parse_date_range("2026-04-21..2026-04-23") == [
        "2026-04-21",
        "2026-04-22",
        "2026-04-23",
    ]


def test_parse_date_rejects_invalid_format() -> None:
    with pytest.raises(CliError, match="expected YYYY-MM-DD"):
        parse_date("04/21/2026")


def test_parse_date_range_rejects_reversed_range() -> None:
    with pytest.raises(CliError, match="range end cannot be before start"):
        parse_date_range("2026-04-23..2026-04-21")


def test_parse_mix_entry_accepts_percent_values() -> None:
    name, values = parse_mix_entry("warning-mix unexpectedFailed=20%")

    assert name == "warning-mix"
    assert len(values) == 1
    assert values[0].key == "unexpectedFailed"
    assert values[0].value == 20
    assert values[0].is_percent is True


def test_parse_mix_entry_rejects_unknown_key() -> None:
    with pytest.raises(CliError, match="unknown result mix key"):
        parse_mix_entry("bad-mix unknownThing=1")


def test_build_mixes_includes_defaults_and_custom_mix() -> None:
    mixes = build_mixes(argparse.Namespace(mix=["warning-mix unexpectedFailed=20%"]))

    assert set(mixes) == {"fixture-default", "all-ok", "warning-mix"}
    assert mixes["warning-mix"][0].key == "unexpectedFailed"


def test_parse_day_entry_supports_fixture_mix_and_count() -> None:
    run_date, specs = parse_day_entry(
        "2026-04-21:basic.nok-warning@warning-mix=2,error=1"
    )

    assert run_date == "2026-04-21"
    assert specs == [
        ("basic", "nok-warning", "warning-mix", 2),
        (None, "error", None, 1),
    ]


def test_parse_day_entry_rejects_unknown_conclusion() -> None:
    with pytest.raises(CliError, match="unknown conclusion"):
        parse_day_entry("2026-04-21:unknown=1")
