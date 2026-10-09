"""Load and auto-discover fixture providers.

User-supplied providers are loaded from a directory via ``--fixture``. When no
``--fixture`` is given, providers are discovered through the ``bublik_e2e.fixtures``
entry-point group, so the bundled providers (and any other installed fixture
package) register themselves automatically.
"""

from __future__ import annotations

import argparse
import importlib.util
from importlib.metadata import entry_points
from pathlib import Path
from typing import Mapping

from core.common import CliError, sanitize_path_part
from core.fixture_api import FixtureProvider

ENTRY_POINT_GROUP = "bublik_e2e.fixtures"
_REQUIRED_ATTRS = ("name", "project", "fixture_id_prefix", "default_mix", "generate")


def _validate(fixture: object, source: str) -> FixtureProvider:
    missing = [attr for attr in _REQUIRED_ATTRS if not hasattr(fixture, attr)]
    if missing:
        raise CliError(f"fixture {source} is missing: {', '.join(sorted(missing))}")
    return fixture  # type: ignore[return-value]


def load_fixture(path: Path) -> FixtureProvider:
    fixture_dir = path.resolve()
    module_path = fixture_dir / "fixture.py"
    if not module_path.is_file():
        raise CliError(f"fixture module not found: {module_path}")

    module_name = f"bublik_fixture_{sanitize_path_part(str(fixture_dir))}"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise CliError(f"cannot load fixture module: {module_path}")

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise CliError(f"failed to load fixture {module_path}: {exc}") from exc

    return _validate(getattr(module, "fixture", None), str(module_path))


def discover_fixtures() -> dict[str, FixtureProvider]:
    """Return every provider registered under the ``bublik_e2e.fixtures`` group."""
    selected: dict[str, FixtureProvider] = {}
    for ep in sorted(entry_points(group=ENTRY_POINT_GROUP), key=lambda e: e.name):
        try:
            fixture = ep.load()
        except Exception as exc:
            raise CliError(f"failed to load fixture {ep.name!r}: {exc}") from exc
        fixture = _validate(fixture, f"entry point {ep.name!r}")
        if fixture.name in selected:
            raise CliError(f"duplicate fixture name {fixture.name!r}")
        selected[fixture.name] = fixture
    return selected


def known_fixture_names(selected: Mapping[str, FixtureProvider]) -> set[str]:
    """Names a plan may refer to: the selection plus every bundled fixture.

    A plan written for the whole campaign stays valid under ``--fixture``.
    """
    return set(selected) | set(discover_fixtures())


def selected_fixtures(args: argparse.Namespace) -> dict[str, FixtureProvider]:
    if not args.fixture:
        selected = discover_fixtures()
        if not selected:
            raise CliError(
                "no fixtures found; reinstall bublik-e2e (bundled providers) "
                "or pass --fixture <dir>"
            )
        return selected

    selected = {}
    for path in args.fixture:
        fixture = load_fixture(Path(path))
        if fixture.name in selected:
            raise CliError(f"duplicate fixture name {fixture.name!r}")
        selected[fixture.name] = fixture
    return selected
