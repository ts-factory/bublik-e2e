"""Public contract for pluggable Bublik fixture providers."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class FixtureProvider(Protocol):
    name: str
    project: str
    fixture_id_prefix: str
    default_mix: str

    def generate(self, output_dir: Path, pretty: bool) -> None:
        """Create meta_data.json and bublik.json in output_dir."""


class BaseFixture:
    """Convenience base with sensible defaults for fixture providers.

    Subclasses only need to set ``name`` and implement ``generate``; override
    ``project``, ``fixture_id_prefix`` or ``default_mix`` when they differ.
    """

    name: str = "fixture"
    project: str = "bublik-e2e"
    fixture_id_prefix: str = "e2e"
    default_mix: str = "fixture-default"
    report_configs: tuple = ()

    def generate(self, output_dir: Path, pretty: bool) -> None:
        raise NotImplementedError
