"""Shared helpers for the Bublik e2e fixture toolkit."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any
import urllib.parse

from rich.console import Console

# Shared consoles so styling/theme stay consistent across the CLI and core.
# Status/progress and human-facing notices go to stderr, keeping stdout clean
# for machine-readable output (e.g. the manifest path printed by `generate`).
console = Console(stderr=True)


class CliError(RuntimeError):
    """Raised for user-facing errors; surfaced as ``error: ...`` on stderr."""


def sanitize_path_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "fixture"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any, pretty: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    destination = path.resolve(strict=False) if path.is_symlink() else path
    destination.parent.mkdir(parents=True, exist_ok=True)
    kwargs = {"indent": 2, "sort_keys": True} if pretty else {"separators": (",", ":")}
    content = json.dumps(payload, **kwargs) + "\n"
    mode = destination.stat().st_mode & 0o777 if destination.exists() else 0o644
    fd, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def normalize_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "")
    )
