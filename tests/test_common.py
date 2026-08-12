from __future__ import annotations

from pathlib import Path

import pytest

from core import common


def test_write_json_preserves_previous_file_if_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "data.json"
    path.write_text('{"old":true}\n')
    real_replace = common.os.replace

    def fail_replace(source: str, destination: Path) -> None:
        if destination == path:
            raise OSError("replace failed")
        real_replace(source, destination)

    monkeypatch.setattr(common.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        common.write_json(path, {"new": True}, pretty=False)

    assert path.read_text() == '{"old":true}\n'
    assert list(tmp_path.iterdir()) == [path]


def test_write_json_preserves_symlink_and_atomically_replaces_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.json"
    target.write_text('{"old":true}\n')
    link = tmp_path / "data.json"
    link.symlink_to(target.name)

    common.write_json(link, {"new": True}, pretty=False)

    assert link.is_symlink()
    assert link.readlink() == Path(target.name)
    assert target.read_text() == '{"new":true}\n'
