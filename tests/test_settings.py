from __future__ import annotations

import argparse
from pathlib import Path

from core.settings import DEFAULT_EMAIL, DEFAULT_PASSWORD, Settings


def make_args(**overrides: object) -> argparse.Namespace:
    defaults: dict[str, object] = {
        "env_file": None,
        "url": None,
        "email": None,
        "password": None,
        "publish_dir": None,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_settings_uses_default_url_when_no_env(monkeypatch) -> None:
    monkeypatch.delenv("BUBLIK_FQDN", raising=False)
    monkeypatch.delenv("BUBLIK_DOCKER_PROXY_PORT", raising=False)
    monkeypatch.delenv("URL_PREFIX", raising=False)

    settings = Settings.from_args(make_args())

    assert settings.base_url == "http://127.0.0.1:42000"
    assert settings.logs_base_url == "http://127.0.0.1:42000/logs"


def test_settings_builds_url_from_env(monkeypatch) -> None:
    monkeypatch.setenv("BUBLIK_FQDN", "https://bublik.example.com/")
    monkeypatch.setenv("BUBLIK_DOCKER_PROXY_PORT", "443")
    monkeypatch.setenv("URL_PREFIX", "/demo/")

    settings = Settings.from_args(make_args())

    assert settings.base_url == "https://bublik.example.com/demo"


def test_settings_cli_overrides_env(monkeypatch) -> None:
    monkeypatch.setenv("BUBLIK_FQDN", "https://env.example.com")
    monkeypatch.setenv("DJANGO_SUPERUSER_EMAIL", "env@example.com")
    monkeypatch.setenv("DJANGO_SUPERUSER_PASSWORD", "env-password")

    settings = Settings.from_args(
        make_args(
            url="http://localhost:9000/",
            email="cli@example.com",
            password="cli-password",
        )
    )

    assert settings.base_url == "http://localhost:9000"
    assert settings.email == "cli@example.com"
    assert settings.password == "cli-password"


def test_settings_uses_default_credentials(monkeypatch) -> None:
    monkeypatch.delenv("DJANGO_SUPERUSER_EMAIL", raising=False)
    monkeypatch.delenv("DJANGO_SUPERUSER_PASSWORD", raising=False)

    settings = Settings.from_args(make_args())

    assert settings.email == DEFAULT_EMAIL
    assert settings.password == DEFAULT_PASSWORD


def test_settings_resolves_relative_publish_dir(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    settings = Settings.from_args(make_args(publish_dir=Path("logs/e2e")))

    assert settings.publish_dir == tmp_path / "logs" / "e2e"
