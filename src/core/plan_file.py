"""Read a plan file into the ``--runs``/``--mix``/``--day`` option values.

Plans are YAML (JSON is valid YAML, so a ``.json`` plan still loads). YAML is
what makes a campaign readable: one run group per line, and comments explaining
why a day looks the way it does.

This module owns loading and duplicate-key detection; :mod:`core.plan_models`
owns the shape; :mod:`core.planning` owns the spec syntax. Nothing is validated
twice.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from pydantic import ValidationError
import yaml

from core.common import CliError
from core.plan_models import Plan


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys.

    Both YAML and JSON keep the last value silently, which would let a plan
    quietly drop a whole day or mix.
    """


def _no_duplicates(loader: _UniqueKeyLoader, node: yaml.MappingNode) -> dict:
    seen: set = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=True)
        try:
            duplicate = key in seen
        except TypeError:  # unhashable key; the model rejects it later
            continue
        if duplicate:
            raise CliError(f"duplicate key {_render_key(key)} in plan file")
        seen.add(key)
    return loader.construct_mapping(node, deep=True)


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicates
)


def _render_key(key: object) -> str:
    """YAML parses a bare 2026-04-19 into a date; show it back as written."""
    return key.isoformat() if isinstance(key, date) else repr(key)


def _format_errors(path: Path, error: ValidationError) -> str:
    lines = [f"invalid plan file {path}:"]
    for item in error.errors():
        parts = [str(part) for part in item["loc"] if str(part)]
        location = ".".join(parts) or "(root)"
        lines.append(f"  {location}: {item['msg']}")
    return "\n".join(lines)


def load_plan(path: Path) -> Plan:
    """Parse and validate ``path``, returning the plan model."""
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except OSError as exc:
        raise CliError(f"cannot read plan file {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise CliError(f"invalid YAML in plan file {path}: {exc}") from exc

    if raw is None:
        raise CliError(f"plan file {path} is empty")
    try:
        return Plan.model_validate(raw)
    except ValidationError as exc:
        raise CliError(_format_errors(path, exc)) from exc


def load_plan_file(path: Path) -> tuple[int | None, list[str], list[str]]:
    """Read ``path`` and return ``(runs, mix_entries, day_entries)``."""
    plan = load_plan(path)
    return plan.runs, plan.mix_options(), plan.day_options()
