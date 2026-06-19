"""Deterministic synthetic Bublik bundle generator for E2E fixtures."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any

from core.fixture_api import BaseFixture


START = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class TestFamily:
    name: str
    objective: str
    parameters: tuple[dict[str, str], ...] = ({},)
    requirements: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    measurements: tuple[dict[str, Any], ...] = ()
    # Per-iteration requirements, aligned by index with ``parameters``. When set,
    # it overrides ``requirements`` so each leaf can carry the exact requirement
    # list of its real-world iteration. Empty means "use ``requirements`` for all".
    iteration_requirements: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class Package:
    name: str
    objective: str
    tests: tuple[TestFamily, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RunProfile:
    name: str
    kind: str
    metas: dict[str, str]
    tags: dict[str, str | None]


# ---------------------------------------------------------------------------
# Data-driven family builders. Fixtures keep authoring objectives inline but
# source the exact per-iteration ``params``/``reqs`` from a generated ``REAL``
# mapping (``{test_name: [{"params": {...}, "reqs": [...]}, ...]}``) produced by
# ``tools/gen_real_data.py`` from real Bublik runs.
# ---------------------------------------------------------------------------

# Mapping of test name -> list of iterations, each ``{"params": ..., "reqs": ...}``.
RealData = dict[str, list[dict[str, Any]]]


def real_family(real: RealData, name: str, objective: str) -> TestFamily:
    """Build one TestFamily for ``name`` from its real iterations.

    Each real iteration becomes a leaf carrying that iteration's exact ``params``
    and ``reqs``. Names absent from ``real`` fall back to a single param-less leaf.
    """
    iterations = real.get(name)
    if not iterations:
        return TestFamily(name, objective)
    return TestFamily(
        name,
        objective,
        parameters=tuple(dict(it["params"]) for it in iterations),
        iteration_requirements=tuple(tuple(it.get("reqs", ())) for it in iterations),
    )


def real_families(real: RealData, objectives: dict[str, str]) -> tuple[TestFamily, ...]:
    """Build a TestFamily per ``name -> objective`` mapping, preserving order."""
    return tuple(
        real_family(real, name, objective) for name, objective in objectives.items()
    )


def _node_status(node: dict[str, Any]) -> str:
    return node["obtained"]["result"]["status"]


def _status_level(status: str) -> str:
    if status == "PASSED":
        return "RING"
    if status in {"FAILED", "KILLED", "CORED"}:
        return "ERROR"
    if status in {"SKIPPED", "FAKED", "INCOMPLETE"}:
        return "WARN"
    return "INFO"


def _time_part(ts: str) -> str:
    return ts.split(" ", 1)[1]


def _duration_str(seconds: float) -> str:
    total_ms = max(0, int(seconds * 1000))
    total_seconds, ms = divmod(total_ms, 1000)
    minutes, seconds_part = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes}:{seconds_part}.{ms:03}"


def _text_content(content: str) -> list[dict[str, Any]]:
    return [{"type": "te-log-table-content-text", "content": content}]


def _mi_content(content: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"type": "te-log-table-content-mi", "content": content}]


def _entity_model(node: dict[str, Any]) -> dict[str, Any]:
    entity = "Package" if node["type"] == "pkg" else "Test"
    model: dict[str, Any] = {
        "id": str(node["test_id"]),
        "name": node["name"],
        "entity": entity,
        "result": _node_status(node),
        "extended_properties": {"path": node["path_str"]},
    }
    if node.get("err"):
        model["error"] = node["err"]
    if node["type"] == "test":
        model["extended_properties"]["tin"] = str(node["tin"])
        model["extended_properties"]["hash"] = node["hash"]
    return model


def _meta_for_node(node: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "start": _time_part(node["start_ts"]),
        "end": _time_part(node["end_ts"]),
        "duration": _duration_str(node["end_ts_utc"] - node["start_ts_utc"]),
    }
    if node.get("objective"):
        payload["objective"] = node["objective"]
    if node.get("params"):
        payload["params"] = node["params"]
    verdicts = node["obtained"]["result"].get("verdicts")
    if verdicts:
        level = _status_level(_node_status(node))
        payload["verdicts"] = [
            {"verdict": verdict, "level": level} for verdict in verdicts
        ]
    if node.get("artifacts"):
        payload["artifacts"] = node["artifacts"]
    if node.get("err"):
        payload["err"] = node["err"]
    return payload


def _table_rows_for_node(node: dict[str, Any]) -> list[dict[str, Any]]:
    next_line_number = 0

    def next_line() -> int:
        nonlocal next_line_number
        next_line_number += 1
        return next_line_number

    def text_row(
        level: str, entity_name: str, user_name: str, ts: str, utc: float, content: str
    ) -> dict[str, Any]:
        return {
            "line_number": next_line(),
            "level": level,
            "entity_name": entity_name,
            "user_name": user_name,
            "timestamp": {"timestamp": utc, "formatted": _time_part(ts)},
            "log_content": _text_content(content),
        }

    children = node.get("iters") or []
    if children:
        rows = []
        for child in children:
            kind = "test" if child["type"] == "test" else "package"
            description = f"{child['name']} {kind} start"
            if child.get("objective"):
                description = f"{description}\n{child['objective']}"
            rows.append(
                text_row(
                    _status_level(_node_status(child)),
                    child["name"],
                    "Step",
                    child["start_ts"],
                    child["start_ts_utc"],
                    description,
                )
            )
        return rows

    main = node["name"]
    status = _node_status(node)
    rows = [
        text_row(
            "INFO", main, "TAPI Jumps", node["start_ts"], node["start_ts_utc"],
            "Main test entity",
        ),
        text_row(
            _status_level(status), main, "Step", node["start_ts"], node["start_ts_utc"],
            f"{node['name']} start"
            + (f"\n{node['objective']}" if node.get("objective") else ""),
        ),
    ]
    for measurement in node.get("measurements") or []:
        rows.append(
            {
                "line_number": next_line(),
                "level": "MI",
                "entity_name": main,
                "user_name": "Artifact",
                "timestamp": {
                    "timestamp": node["start_ts_utc"],
                    "formatted": _time_part(node["start_ts"]),
                },
                "log_content": _mi_content(measurement),
            }
        )
    rows.append(
        text_row(
            _status_level(status), "Tester", "Run", node["end_ts"], node["end_ts_utc"],
            f"Obtained result is:\n{status}",
        )
    )
    return rows


def _log_json_for_node(node: dict[str, Any]) -> dict[str, Any]:
    content: list[dict[str, Any]] = [
        {
            "type": "te-log-meta",
            "entity_model": _entity_model(node),
            "meta": _meta_for_node(node),
        }
    ]
    children = node.get("iters") or []
    if children:
        content.append(
            {
                "type": "te-log-entity-list",
                "items": [_entity_model(child) for child in children],
            }
        )
    content.append({"type": "te-log-table", "data": _table_rows_for_node(node)})
    return {"version": "v1", "root": [{"type": "te-log", "content": content}]}


def _tree_entry(
    node: dict[str, Any], file_name: str, child_files: list[str]
) -> dict[str, Any]:
    status = _node_status(node)
    entry: dict[str, Any] = {
        "id": file_name,
        "name": node["name"],
        "has_error": status not in {"PASSED", "SKIPPED"},
        "skipped": status == "SKIPPED",
        "entity": node["type"],
    }
    if child_files:
        entry["children"] = child_files
    return entry


class SyntheticFixture(BaseFixture):
    def __init__(
        self,
        *,
        name: str,
        project: str,
        revision_meta: str,
        revision_url: str,
        packages: tuple[Package, ...],
        tags: dict[str, str | None],
        tests: tuple[TestFamily, ...] = (),
        profiles: tuple[RunProfile, ...] = (),
        root_objective: str | None = None,
        report_configs: tuple[dict[str, Any], ...] = (),
    ) -> None:
        self.name = name
        self.project = project
        self.revision_meta = revision_meta
        self.revision_url = revision_url
        self.packages = packages
        # Leaf tests emitted directly under the run root (e.g. a top-level
        # ``prologue``), matching real suites that log these as tests, not packages.
        self.tests = tests
        self.tags = tags
        self.profiles = profiles
        self.root_objective = root_objective
        self.report_configs = report_configs

    def profile_for(self, conclusion: str, ordinal: int) -> RunProfile | None:
        if not self.profiles:
            return None
        preferences = {
            "ok": ("ok", "warning"),
            "nok-warning": ("result-error", "warning", "ok"),
            "nok-error": ("result-error", "status-error", "ok"),
            "error": ("status-error", "result-error", "ok"),
            "warning": ("warning", "ok"),
        }.get(conclusion, ("ok", "result-error", "status-error", "warning"))

        for kind in preferences:
            candidates = [profile for profile in self.profiles if profile.kind == kind]
            if candidates:
                return candidates[(ordinal - 1) % len(candidates)]
        return self.profiles[(ordinal - 1) % len(self.profiles)]

    def build_plan(self, node: dict[str, Any]) -> dict[str, Any]:
        plan: dict[str, Any] = {"name": node["name"], "type": node["type"]}
        children = node.get("iters") or []
        if children:
            plan["children"] = [self.build_plan(child) for child in children]
        return plan

    def generate(self, output_dir: Path, pretty: bool) -> None:
        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True)

        counter = 2
        elapsed = 0

        def timestamp(offset: int) -> tuple[str, float]:
            value = START + timedelta(seconds=offset)
            return (
                value.strftime("%Y.%m.%d %H:%M:%S.%f")[:-3],
                value.timestamp(),
            )

        def make_leaf(
            path_prefix: list[str],
            family: TestFamily,
            parameters: dict[str, str],
            tin: int,
        ) -> dict[str, Any]:
            nonlocal counter, elapsed
            test_id = counter
            counter += 1
            start_text, start_utc = timestamp(elapsed)
            elapsed += 2
            end_text, end_utc = timestamp(elapsed)
            elapsed += 1
            path = [*path_prefix, family.name]
            identity = json.dumps([path, parameters, tin], sort_keys=True)
            reqs = (
                family.iteration_requirements[tin]
                if family.iteration_requirements
                else family.requirements
            )

            result: dict[str, Any] = {
                "status": "PASSED",
                "verdicts": [],
                "artifacts": list(family.artifacts),
            }
            return {
                "iters": [],
                "start_ts": start_text,
                "start_ts_utc": start_utc,
                "name": family.name,
                "type": "test",
                "hash": hashlib.md5(identity.encode(), usedforsecurity=False).hexdigest(),
                "test_id": test_id,
                "plan_id": test_id,
                "tin": tin,
                "reqs": list(reqs),
                "objective": family.objective,
                "params": parameters,
                "path": path,
                "path_str": "/".join(path),
                "end_ts": end_text,
                "end_ts_utc": end_utc,
                "err": "",
                "obtained": {"result": result},
                "measurements": list(family.measurements),
            }

        # Root-level leaf tests run first (e.g. a top-level prologue), so build them
        # before the packages to keep the synthetic timeline in execution order.
        root_test_nodes = [
            make_leaf([self.name], family, parameters, tin)
            for family in self.tests
            for tin, parameters in enumerate(family.parameters)
        ]

        package_nodes: list[dict[str, Any]] = []
        for package in self.packages:
            children = [
                make_leaf([self.name, package.name], family, parameters, tin)
                for family in package.tests
                for tin, parameters in enumerate(family.parameters)
            ]
            start_text = children[0]["start_ts"]
            start_utc = children[0]["start_ts_utc"]
            end_text = children[-1]["end_ts"]
            end_utc = children[-1]["end_ts_utc"]
            package_id = counter
            counter += 1
            package_nodes.append(
                {
                    "iters": children,
                    "start_ts": start_text,
                    "start_ts_utc": start_utc,
                    "name": package.name,
                    "type": "pkg",
                    "hash": hashlib.md5(
                        f"{self.name}/{package.name}".encode(),
                        usedforsecurity=False,
                    ).hexdigest(),
                    "test_id": package_id,
                    "plan_id": package_id,
                    "tin": -1,
                    "reqs": [],
                    "objective": package.objective,
                    "params": {},
                    "path": [self.name, package.name],
                    "path_str": f"{self.name}/{package.name}",
                    "end_ts": end_text,
                    "end_ts_utc": end_utc,
                    "err": "",
                    "obtained": {"result": {"status": "PASSED"}},
                }
            )

        child_nodes = root_test_nodes + package_nodes
        root = {
            "iters": child_nodes,
            "start_ts": child_nodes[0]["start_ts"],
            "start_ts_utc": child_nodes[0]["start_ts_utc"],
            "name": self.name,
            "type": "pkg",
            "hash": hashlib.md5(self.name.encode(), usedforsecurity=False).hexdigest(),
            "test_id": 1,
            "plan_id": 0,
            "tin": -1,
            "reqs": [],
            "objective": self.root_objective
            or f"Synthetic {self.name} qualification run.",
            "params": {},
            "path": [self.name],
            "path_str": self.name,
            "end_ts": child_nodes[-1]["end_ts"],
            "end_ts_utc": child_nodes[-1]["end_ts_utc"],
            "err": "",
            "obtained": {"result": {"status": "PASSED"}},
        }
        finish = START + timedelta(seconds=elapsed)
        metadata = {
            "version": 1,
            "metas": [
                {"name": "TS_NAME", "value": self.name},
                {"name": f"{self.revision_meta}_GIT_URL", "value": self.revision_url},
                {
                    "name": f"{self.revision_meta}_BRANCH",
                    "value": "main",
                    "type": "branch",
                },
                {
                    "name": f"{self.revision_meta}_REV",
                    "value": hashlib.sha1(self.name.encode(), usedforsecurity=False).hexdigest(),
                    "type": "revision",
                },
                {"name": "CFG", "value": "synthetic-e2e"},
                {"name": "START_TIMESTAMP", "value": START.isoformat()},
                {"name": "CAMPAIGN_DATE", "value": START.date().isoformat()},
                {"name": "RUN_STATUS", "value": "DONE"},
                {
                    "name": "FINISH_TIMESTAMP",
                    "value": finish.isoformat(),
                    "type": "timestamp",
                },
                {"name": "PROJECT", "value": self.project},
            ],
        }
        indent = 2 if pretty else None
        separators = None if pretty else (",", ":")
        (output_dir / "meta_data.json").write_text(
            json.dumps(metadata, indent=indent, separators=separators) + "\n",
            encoding="utf-8",
        )
        (output_dir / "bublik.json").write_text(
            json.dumps(
                {
                    "start_ts": root["start_ts"],
                    "end_ts": root["end_ts"],
                    "plan": self.build_plan(root),
                    "iters": [root],
                    "tags": self.tags,
                },
                indent=indent,
                separators=separators,
            )
            + "\n",
            encoding="utf-8",
        )

        # Per-node log bundle (json/tree.json + json/node_*.json). Without it an
        # imported run shows the tree but every node's log is empty; this emits a
        # static log table for each node, mirroring the basic fixture's converter.
        json_dir = output_dir / "json"
        json_dir.mkdir()
        tree: dict[str, dict[str, Any]] = {}

        def write_json(path: Path, payload: dict[str, Any]) -> None:
            path.write_text(
                json.dumps(payload, indent=indent, separators=separators) + "\n",
                encoding="utf-8",
            )

        def write_node(node: dict[str, Any], is_root: bool = False) -> str:
            file_name = "node_1_0.json" if is_root else f"node_id{node['test_id']}.json"
            child_files = [write_node(child) for child in node.get("iters") or []]
            payload = _log_json_for_node(node)
            write_json(json_dir / file_name, payload)
            if is_root:
                write_json(json_dir / "node_id1.json", payload)
            tree[file_name] = _tree_entry(node, file_name, child_files)
            return file_name

        write_node(root, is_root=True)
        write_json(
            json_dir / "tree.json",
            {"main_package": "node_1_0.json", "tree": tree},
        )
