"""Settings derived from CLI flags and environment variables.

Configuration is instance-agnostic: there is no bublik-docker project-root
discovery and no automatic ``.env`` reading. Values come from CLI flag overrides
first, then real environment variables (optionally seeded from an explicit
``--env-file``). The recognised env-var names are the existing bublik-docker
ones (``BUBLIK_FQDN``, ``BUBLIK_DOCKER_PROXY_PORT``, ``URL_PREFIX``,
``DJANGO_SUPERUSER_EMAIL``, ``DJANGO_SUPERUSER_PASSWORD``) so a docker ``.env``
carries over unchanged. The publish directory is an explicit full path
(``--publish-dir`` / ``BUBLIK_E2E_PUBLISH_DIR``); nothing is assumed about its
layout.
The run-log schema is likewise explicit (``--run-log-schema`` /
``BUBLIK_E2E_RUN_LOG_SCHEMA``) and mandatory for commands that generate bundles.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import timedelta, timezone
import os
from pathlib import Path

DEFAULT_TIMEZONE = timezone(timedelta(hours=3))
DEFAULT_EMAIL = "admin@bublik.com"
DEFAULT_PASSWORD = "admin"


def default_manifest() -> Path:
    """Default manifest path (CWD-relative), consumed by the Playwright suite."""
    return Path.cwd() / ".e2e" / "e2e-manifest.json"


def resolve_manifest(args: argparse.Namespace) -> Path:
    """The ``--manifest`` path if given, else the default."""
    explicit = getattr(args, "manifest", None)
    return explicit if explicit is not None else default_manifest()


def _parse_env_file(env_file: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_file.exists():
        return values
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"').strip("'")
    return values


@dataclass
class Settings:
    values: dict[str, str]
    url_override: str | None = None
    email_override: str | None = None
    password_override: str | None = None
    publish_dir_override: Path | None = None
    run_log_schema_override: Path | None = None

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "Settings":
        env_file = getattr(args, "env_file", None)
        values: dict[str, str] = {}
        if env_file is not None:
            values.update(_parse_env_file(Path(env_file)))
        values.update(os.environ)
        return cls(
            values=values,
            url_override=getattr(args, "url", None),
            email_override=getattr(args, "email", None),
            password_override=getattr(args, "password", None),
            publish_dir_override=getattr(args, "publish_dir", None),
            run_log_schema_override=getattr(args, "run_log_schema", None),
        )

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.values.get(key, default)

    # --- credentials ---------------------------------------------------------

    @property
    def email(self) -> str:
        return (
            self.email_override or self.get("DJANGO_SUPERUSER_EMAIL") or DEFAULT_EMAIL
        )

    @property
    def password(self) -> str:
        return (
            self.password_override
            or self.get("DJANGO_SUPERUSER_PASSWORD")
            or DEFAULT_PASSWORD
        )

    # --- paths ---------------------------------------------------------------

    @property
    def publish_dir(self) -> Path | None:
        """Explicit full path bundles are written to.

        The directory is served by the instance at ``{url}/logs/<name>/`` — its
        basename is the URL segment, and no ``logs/logs`` nesting is assumed.
        Comes from ``--publish-dir`` or ``BUBLIK_E2E_PUBLISH_DIR``; ``None`` when
        neither is set (callers that need it raise a clear error).
        """
        raw = self.publish_dir_override
        if raw is None:
            env = self.get("BUBLIK_E2E_PUBLISH_DIR")
            raw = Path(env) if env else None
        if raw is None:
            return None
        # Resolve to absolute: fixtures may shell out with their own cwd, so a
        # relative bundle path would be written in the wrong place.
        path = Path(raw)
        return path if path.is_absolute() else Path.cwd() / path

    @property
    def run_log_schema(self) -> Path | None:
        raw = self.run_log_schema_override
        if raw is None:
            env = self.get("BUBLIK_E2E_RUN_LOG_SCHEMA")
            raw = Path(env) if env else None
        if raw is None:
            return None
        path = Path(raw)
        return path if path.is_absolute() else Path.cwd() / path

    # --- URLs ----------------------------------------------------------------

    @property
    def base_url(self) -> str:
        if self.url_override:
            return self.url_override.rstrip("/")
        fqdn = (self.get("BUBLIK_FQDN", "http://127.0.0.1") or "").rstrip("/")
        port = self.get("BUBLIK_DOCKER_PROXY_PORT", "42000")
        prefix = (self.get("URL_PREFIX", "") or "").strip("/")
        if port and port not in {"80", "443"}:
            fqdn = f"{fqdn}:{port}"
        if prefix:
            fqdn = f"{fqdn}/{prefix}"
        return fqdn.rstrip("/")

    @property
    def logs_base_url(self) -> str:
        return f"{self.base_url}/logs"

    @property
    def ui_base_url(self) -> str:
        return f"{self.base_url}/v2"

    @property
    def dashboard_url(self) -> str:
        return f"{self.ui_base_url}/dashboard"

    @property
    def history_url(self) -> str:
        return f"{self.ui_base_url}/history"

    @property
    def run_url_template(self) -> str:
        return f"{self.ui_base_url}/runs/{{runId}}"

    @property
    def log_url_template(self) -> str:
        return f"{self.ui_base_url}/log/{{runId}}"
