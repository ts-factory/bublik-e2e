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
        profiles: tuple[RunProfile, ...] = (),
    ) -> None:
        self.name = name
        self.project = project
        self.revision_meta = revision_meta
        self.revision_url = revision_url
        self.packages = packages
        self.tags = tags
        self.profiles = profiles

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
            package_name: str,
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
            path = [self.name, package_name, family.name]
            identity = json.dumps([path, parameters, tin], sort_keys=True)

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
                "reqs": list(family.requirements),
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

        package_nodes: list[dict[str, Any]] = []
        for package in self.packages:
            children = [
                make_leaf(package.name, family, parameters, tin)
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

        root = {
            "iters": package_nodes,
            "start_ts": package_nodes[0]["start_ts"],
            "start_ts_utc": package_nodes[0]["start_ts_utc"],
            "name": self.name,
            "type": "pkg",
            "hash": hashlib.md5(self.name.encode(), usedforsecurity=False).hexdigest(),
            "test_id": 1,
            "plan_id": 0,
            "tin": -1,
            "reqs": [],
            "objective": f"Synthetic {self.name} qualification run.",
            "params": {},
            "path": [self.name],
            "path_str": self.name,
            "end_ts": package_nodes[-1]["end_ts"],
            "end_ts_utc": package_nodes[-1]["end_ts_utc"],
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
