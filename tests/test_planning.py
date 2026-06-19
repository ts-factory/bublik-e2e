from __future__ import annotations

import argparse
from dataclasses import dataclass

import pytest

from core.common import CliError
from core.planning import (
    build_mixes,
    build_plan,
    parse_date,
    parse_date_range,
    parse_day_entry,
    parse_mix_entry,
)


@dataclass
class _StubFixture:
    name: str
    default_mix: str = "fixture-default"


def _fixtures(*names: str) -> dict[str, _StubFixture]:
    return {name: _StubFixture(name=name) for name in names}


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


def test_parse_mix_entry_accepts_colon_syntax() -> None:
    name, values = parse_mix_entry(
        "warning-mix:unexpectedFailed=20%,unexpectedSkipped=5%"
    )

    assert name == "warning-mix"
    assert [v.key for v in values] == ["unexpectedFailed", "unexpectedSkipped"]
    assert values[0].value == 20
    assert values[1].value == 5


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
        ("basic", "nok-warning", "warning-mix", None, 2),
        (None, "error", None, None, 1),
    ]


def test_parse_day_entry_parses_inline_mix() -> None:
    run_date, specs = parse_day_entry(
        "2026-04-21:net-drv-ts.nok-warning@unexpectedFailed=20%;unexpectedSkipped=5%=2"
    )

    assert run_date == "2026-04-21"
    assert len(specs) == 1
    fixture_name, conclusion, mix_name, mix_values, count = specs[0]
    assert (fixture_name, conclusion, mix_name, count) == (
        "net-drv-ts",
        "nok-warning",
        None,
        2,
    )
    assert [v.key for v in mix_values] == ["unexpectedFailed", "unexpectedSkipped"]
    assert mix_values[0].value == 20
    assert mix_values[0].is_percent is True


def test_parse_day_entry_rejects_unknown_conclusion() -> None:
    with pytest.raises(CliError, match="unknown conclusion"):
        parse_day_entry("2026-04-21:unknown=1")


def test_build_plan_derives_runs_from_day_specs() -> None:
    args = argparse.Namespace(
        day=["2026-04-21:basic.ok=2,dpdk.nok-warning=1"],
        fill=None,
        dates=None,
        runs=None,
        mix=None,
    )
    planned, _ = build_plan(args, _fixtures("basic", "dpdk"), build_mixes(args))

    assert len(planned) == 3


def test_build_plan_asserts_explicit_runs_match() -> None:
    args = argparse.Namespace(
        day=["2026-04-21:basic.ok=2"],
        fill=None,
        dates=None,
        runs=5,
        mix=None,
    )
    with pytest.raises(CliError, match="fixture plan contains 2 runs"):
        build_plan(args, _fixtures("basic"), build_mixes(args))


def test_build_plan_registers_inline_mix() -> None:
    args = argparse.Namespace(
        day=["2026-04-21:basic.nok-warning@unexpectedFailed=20%=2"],
        fill=None,
        dates=None,
        runs=None,
        mix=None,
    )
    mixes = build_mixes(args)
    planned, _ = build_plan(args, _fixtures("basic"), mixes)

    assert len(planned) == 2
    inline_name = planned[0].mix_name
    assert inline_name in mixes
    assert mixes[inline_name][0].key == "unexpectedFailed"


def test_build_plan_fill_requires_runs() -> None:
    args = argparse.Namespace(
        day=None,
        fill="ok",
        dates="2026-04-01..2026-04-03",
        runs=None,
        mix=None,
    )
    with pytest.raises(CliError, match="--fill requires --runs"):
        build_plan(args, _fixtures("basic"), build_mixes(args))
