from __future__ import annotations

from pathlib import Path

import pytest

from ops.core.config import (
    load_global_config,
    load_service_config,
)
from ops.core.discovery import discover_services


def test_load_global_config_defaults_version_to_18(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "DB_DEFAULT_POSTGRES_PASSWORD=secret\n",
        encoding="utf-8",
    )

    config = load_global_config(tmp_path)

    assert config.postgres_version == "18"
    assert config.default_postgres_password == "secret"


def test_discover_services_returns_sorted_names(tmp_path: Path) -> None:
    services_dir = tmp_path / "services"
    services_dir.mkdir()
    (services_dir / ".env.beta").write_text("", encoding="utf-8")
    (services_dir / ".env.alpha").write_text("", encoding="utf-8")

    assert discover_services(tmp_path) == ["alpha", "beta"]


def test_load_service_config_rejects_unknown_keys(tmp_path: Path) -> None:
    services_dir = tmp_path / "services"
    services_dir.mkdir()
    (services_dir / ".env.test").write_text(
        "POSTGRES_USER=admin\n"
        "POSTGRES_PASSWORD=secret\n"
        "POSTGRES_PORT=5401\n"
        "POSTGRES_UNKNOWN=boom\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unsupported keys"):
        load_service_config(tmp_path, "test")


def test_load_service_config_validates_required_fields_and_types(tmp_path: Path) -> None:
    services_dir = tmp_path / "services"
    services_dir.mkdir()
    (services_dir / ".env.test").write_text(
        "POSTGRES_USER=admin\n"
        "POSTGRES_PASSWORD=secret\n"
        "POSTGRES_PORT=5401\n"
        "POSTGRES_MAX_CONNECTIONS=10000\n"
        "POSTGRES_MEMORY_LIMIT=512M\n"
        "POSTGRES_CPU_LIMIT=1.50\n",
        encoding="utf-8",
    )

    config = load_service_config(tmp_path, "test")

    assert config.name == "test"
    assert config.postgres_user == "admin"
    assert config.postgres_password == "secret"
    assert config.postgres_port == 5401
    assert config.max_connections == 10000
    assert config.memory_limit == "512M"
    assert config.cpu_limit == "1.50"


def test_load_service_config_rejects_invalid_port(tmp_path: Path) -> None:
    services_dir = tmp_path / "services"
    services_dir.mkdir()
    (services_dir / ".env.test").write_text(
        "POSTGRES_USER=admin\n"
        "POSTGRES_PASSWORD=secret\n"
        "POSTGRES_PORT=80\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="1024..65535"):
        load_service_config(tmp_path, "test")
