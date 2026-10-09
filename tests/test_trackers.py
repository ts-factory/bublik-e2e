"""Per-fixture issue trackers: plan field -> manifest ``projects`` -> references config."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from cli import app
from core import importer
from core.common import CliError
from core.plan_file import load_plan
from core.trackers import E2E_BUGS, project_trackers

runner = CliRunner()

TRACKERS_PLAN = """
version: 1
fixtures:
  net-drv-ts:
    trackers:
      - {id: NET_BUGS, uri: "https://net.example.invalid/browse/"}
      - {id: E2E_BUGS, name: E2E Bug Tracker, uri: "https://bugs.example.invalid/issue/"}
  dpdk-ethdev-ts:
    trackers: []
days:
  2026-04-20:
    - basic.ok=1
"""


def write_plan(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "plan.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_plan_declares_trackers_per_fixture(tmp_path: Path) -> None:
    plan = load_plan(write_plan(tmp_path, TRACKERS_PLAN))

    assert plan.tracker_spec() == {
        "net-drv-ts": [
            {
                "id": "NET_BUGS",
                "name": "NET_BUGS",
                "uri": "https://net.example.invalid/browse/",
            },
            {
                "id": "E2E_BUGS",
                "name": "E2E Bug Tracker",
                "uri": "https://bugs.example.invalid/issue/",
            },
        ],
        "dpdk-ethdev-ts": [],
    }


@pytest.mark.parametrize(
    ("trackers", "message"),
    [
        (
            '[{id: A, uri: "https://a.invalid/"}, {id: A, uri: "https://b.invalid/"}]',
            "duplicate tracker id A",
        ),
        ('[{id: "has space", uri: "https://a.invalid/"}]', "trackers.0.id"),
        ('[{id: A, uri: "ftp://a.invalid/"}]', "trackers.0.uri"),
    ],
)
def test_an_invalid_tracker_names_its_fixture(
    tmp_path: Path, trackers: str, message: str
) -> None:
    plan = f"""
version: 1
fixtures:
  net-drv-ts:
    trackers: {trackers}
days:
  2026-04-20: [basic.ok=1]
"""
    with pytest.raises(CliError) as error:
        load_plan(write_plan(tmp_path, plan))

    assert "fixtures.net-drv-ts" in str(error.value)
    assert message in str(error.value)


def _fixture(name: str, project: str) -> SimpleNamespace:
    return SimpleNamespace(name=name, project=project)


FIXTURES = {
    "basic": _fixture("basic", "bublik-e2e"),
    "net-drv-ts": _fixture("net-drv-ts", "tsf/net-drv"),
    "dpdk-ethdev-ts": _fixture("dpdk-ethdev-ts", "tsf/dpdk-ethdev"),
}
NET = {"id": "NET_BUGS", "name": "NET_BUGS", "uri": "https://net.example.invalid/"}


def test_a_plan_without_trackers_gives_every_project_the_default() -> None:
    assert project_trackers(FIXTURES, {}) == [
        {"name": "bublik-e2e", "fixtures": ["basic"], "trackers": [E2E_BUGS]},
        {
            "name": "tsf/dpdk-ethdev",
            "fixtures": ["dpdk-ethdev-ts"],
            "trackers": [E2E_BUGS],
        },
        {"name": "tsf/net-drv", "fixtures": ["net-drv-ts"], "trackers": [E2E_BUGS]},
    ]
    assert E2E_BUGS == {
        "id": "E2E_BUGS",
        "name": "E2E Bug Tracker",
        "uri": "https://bugs.example.invalid/issue/",
    }


def test_declared_trackers_replace_the_default_in_order() -> None:
    projects = project_trackers(
        FIXTURES, {"net-drv-ts": [NET, E2E_BUGS], "dpdk-ethdev-ts": []}
    )

    by_name = {project["name"]: project["trackers"] for project in projects}
    assert by_name == {
        "bublik-e2e": [E2E_BUGS],
        "tsf/dpdk-ethdev": [],
        "tsf/net-drv": [NET, E2E_BUGS],
    }


def test_trackers_for_an_unknown_fixture_are_rejected() -> None:
    with pytest.raises(CliError, match="fixtures.nope"):
        project_trackers(FIXTURES, {"nope": []})


def test_trackers_for_a_known_but_unselected_fixture_are_ignored() -> None:
    # `--fixture basic` with a plan that also configures net-drv-ts.
    selected = {"basic": FIXTURES["basic"]}

    assert project_trackers(selected, {"net-drv-ts": [NET], "basic": [NET]}) == [
        {"name": "bublik-e2e", "fixtures": ["basic"], "trackers": [NET]},
    ]
    with pytest.raises(CliError, match="fixtures.nope"):
        project_trackers(selected, {"net-drv-ts": [NET], "nope": []})


def test_fixtures_sharing_a_project_must_agree_on_trackers() -> None:
    fixtures = {**FIXTURES, "basic-two": _fixture("basic-two", "bublik-e2e")}

    with pytest.raises(CliError, match="basic, basic-two"):
        project_trackers(fixtures, {"basic": [NET]})
    assert project_trackers(fixtures, {"basic": [NET], "basic-two": [NET]})[0] == {
        "name": "bublik-e2e",
        "fixtures": ["basic", "basic-two"],
        "trackers": [NET],
    }


class FakeBublik:
    """Just enough of /api/v2/projects/ and /api/v2/config/ for --setup-projects."""

    def __init__(self) -> None:
        self.projects: list[dict] = []
        self.configs: list[dict] = []

    def __call__(self, url: str, method: str = "GET", payload=None, **_) -> object:
        path = url.removeprefix("http://host/api/v2/")
        if path == "projects/":
            if method == "POST":
                self.projects.append({"id": len(self.projects) + 1, **payload})
                return self.projects[-1]
            return list(self.projects)
        if path == "config/":
            if method == "POST":
                self.configs.append({"id": len(self.configs) + 1, **payload})
                return self.configs[-1]
            return [dict(config) for config in self.configs]
        config_id = int(path.removeprefix("config/").rstrip("/"))
        assert method == "PATCH", (method, url)
        next(c for c in self.configs if c["id"] == config_id).update(payload)
        return {}

    def issues(self, project: str) -> dict:
        project_id = next(p["id"] for p in self.projects if p["name"] == project)
        return next(
            c["content"]["ISSUES"]
            for c in self.configs
            if c["name"] == "references" and c["project"] == project_id
        )


def _setup_manifest(projects: list[dict] | None) -> dict:
    manifest: dict = {
        "bundles": [
            {"project": "tsf/net-drv"},
            {"project": "tsf/dpdk-ethdev"},
            {"project": "bublik-e2e"},
        ],
        "configs": [],
    }
    if projects is not None:
        manifest["projects"] = projects
    return manifest


def _setup_projects(
    monkeypatch: pytest.MonkeyPatch, bublik: FakeBublik, manifest
) -> None:
    monkeypatch.setattr(importer, "curl_json", bublik)
    importer.ensure_api_projects(manifest, "http://host", Path("cookies"))


PLANNED = project_trackers(
    FIXTURES, {"net-drv-ts": [NET, E2E_BUGS], "dpdk-ethdev-ts": []}
)
NET_ISSUES = {
    "NET_BUGS": {"name": "NET_BUGS", "uri": "https://net.example.invalid/"},
    "E2E_BUGS": {
        "name": "E2E Bug Tracker",
        "uri": "https://bugs.example.invalid/issue/",
    },
}


def test_setup_projects_writes_each_projects_trackers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bublik = FakeBublik()

    _setup_projects(monkeypatch, bublik, _setup_manifest(PLANNED))

    assert bublik.issues("tsf/net-drv") == NET_ISSUES
    assert list(bublik.issues("tsf/net-drv")) == ["NET_BUGS", "E2E_BUGS"]
    assert bublik.issues("tsf/dpdk-ethdev") == {}
    assert bublik.issues("bublik-e2e") == {"E2E_BUGS": NET_ISSUES["E2E_BUGS"]}


def test_setup_projects_converges_on_a_changed_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bublik = FakeBublik()
    # An instance seeded before trackers were configurable: E2E_BUGS everywhere.
    _setup_projects(monkeypatch, bublik, _setup_manifest(None))
    assert bublik.issues("tsf/dpdk-ethdev") == {"E2E_BUGS": NET_ISSUES["E2E_BUGS"]}
    references = [c["id"] for c in bublik.configs if c["name"] == "references"]

    _setup_projects(monkeypatch, bublik, _setup_manifest(PLANNED))

    # Updated in place, not skipped and not re-created alongside.
    assert [c["id"] for c in bublik.configs if c["name"] == "references"] == references
    assert bublik.issues("tsf/net-drv") == NET_ISSUES
    assert bublik.issues("tsf/dpdk-ethdev") == {}


def test_the_manifest_records_each_projects_trackers(tmp_path: Path) -> None:
    schema = tmp_path / "schema.json"
    schema.write_text('{"type": "object"}', encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    plan = write_plan(tmp_path, TRACKERS_PLAN)

    result = runner.invoke(
        app,
        [
            "generate",
            "--plan",
            str(plan),
            "--publish-dir",
            str(tmp_path / "publish"),
            "--manifest",
            str(manifest_path),
            "--run-log-schema",
            str(schema),
            "--meta-data-schema",
            str(schema),
        ],
    )

    assert result.exit_code == 0, result.output
    projects = json.loads(manifest_path.read_text())["projects"]
    assert {p["name"]: [t["id"] for t in p["trackers"]] for p in projects} == {
        "bublik-e2e": ["E2E_BUGS"],
        "tsf/dpdk-ethdev": [],
        "tsf/net-drv": ["NET_BUGS", "E2E_BUGS"],
    }


def test_plan_command_rejects_trackers_for_an_unknown_fixture(tmp_path: Path) -> None:
    plan = write_plan(
        tmp_path,
        TRACKERS_PLAN.replace("dpdk-ethdev-ts:", "dpdk:"),
    )

    result = runner.invoke(app, ["plan", "--plan", str(plan)])

    assert result.exit_code == 1
    assert "fixtures.dpdk" in result.output
