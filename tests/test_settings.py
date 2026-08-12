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
        "run_log_schema": None,
        "meta_data_schema": None,
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


def test_run_log_schema_uses_env_file_value(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BUBLIK_E2E_RUN_LOG_SCHEMA", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("BUBLIK_E2E_RUN_LOG_SCHEMA=schemas/run-log.json\n")

    settings = Settings.from_args(make_args(env_file=env_file))

    assert settings.run_log_schema == tmp_path / "schemas" / "run-log.json"


def test_schema_paths_are_derived_from_bublik_django_root(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BUBLIK_E2E_RUN_LOG_SCHEMA", raising=False)
    monkeypatch.delenv("BUBLIK_E2E_META_DATA_SCHEMA", raising=False)
    monkeypatch.setenv("BUBLIK_DJANGO_ROOT", "bublik-checkout")

    settings = Settings.from_args(make_args())

    schema_dir = tmp_path / "bublik-checkout" / "bublik" / "data" / "schemas"
    assert settings.run_log_schema == schema_dir / "run_log.json"
    assert settings.meta_data_schema == schema_dir / "meta_data.json"


def test_bublik_django_root_uses_env_file_value(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BUBLIK_DJANGO_ROOT", raising=False)
    monkeypatch.delenv("BUBLIK_E2E_RUN_LOG_SCHEMA", raising=False)
    monkeypatch.delenv("BUBLIK_E2E_META_DATA_SCHEMA", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("BUBLIK_DJANGO_ROOT=bublik-checkout\n")

    settings = Settings.from_args(make_args(env_file=env_file))

    schema_dir = tmp_path / "bublik-checkout" / "bublik" / "data" / "schemas"
    assert settings.run_log_schema == schema_dir / "run_log.json"
    assert settings.meta_data_schema == schema_dir / "meta_data.json"


def test_direct_schema_environment_overrides_bublik_django_root(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BUBLIK_DJANGO_ROOT", "bublik-checkout")
    monkeypatch.setenv("BUBLIK_E2E_RUN_LOG_SCHEMA", "schemas/run-log.json")
    monkeypatch.setenv("BUBLIK_E2E_META_DATA_SCHEMA", "schemas/meta-data.json")

    settings = Settings.from_args(make_args())

    assert settings.run_log_schema == tmp_path / "schemas" / "run-log.json"
    assert settings.meta_data_schema == tmp_path / "schemas" / "meta-data.json"


def test_run_log_schema_cli_overrides_environment(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BUBLIK_E2E_RUN_LOG_SCHEMA", "env-schema.json")
    env_file = tmp_path / ".env"
    env_file.write_text("BUBLIK_E2E_RUN_LOG_SCHEMA=file-schema.json\n")

    settings = Settings.from_args(
        make_args(env_file=env_file, run_log_schema=Path("cli-schema.json"))
    )

    assert settings.run_log_schema == tmp_path / "cli-schema.json"


def test_meta_data_schema_uses_env_file_value(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BUBLIK_E2E_META_DATA_SCHEMA", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("BUBLIK_E2E_META_DATA_SCHEMA=schemas/meta-data.json\n")

    settings = Settings.from_args(make_args(env_file=env_file))

    assert settings.meta_data_schema == tmp_path / "schemas" / "meta-data.json"


def test_meta_data_schema_cli_overrides_environment(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BUBLIK_E2E_META_DATA_SCHEMA", "env-schema.json")
    env_file = tmp_path / ".env"
    env_file.write_text("BUBLIK_E2E_META_DATA_SCHEMA=file-schema.json\n")

    settings = Settings.from_args(
        make_args(env_file=env_file, meta_data_schema=Path("cli-schema.json"))
    )

    assert settings.meta_data_schema == tmp_path / "cli-schema.json"
