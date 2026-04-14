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
    assert config.remote_host is None
    assert config.remote_port == 22
    assert config.remote_user is None
    assert config.remote_password is None
    assert config.remote_backup_path is None
    assert config.backup_enabled is None
    assert config.backup_schedule is None
    assert config.backup_format is None
    assert config.backup_timeout_seconds is None
    assert config.backup_max_days is None
    assert config.backup_max_files is None


def test_load_global_config_reads_remote_settings(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "DB_POSTGRES_VERSION=17.6\n"
        "DB_DEFAULT_POSTGRES_PASSWORD=secret\n"
        "DB_REMOTE_HOST= 2.26.53.128 \n"
        "DB_REMOTE_PORT= 2222 \n"
        "DB_REMOTE_USER= root \n"
        "DB_REMOTE_PASSWORD= pass \n"
        "DB_REMOTE_BACKUP_PATH= /root/backups \n",
        encoding="utf-8",
    )

    config = load_global_config(tmp_path)

    assert config.postgres_version == "17.6"
    assert config.remote_host == "2.26.53.128"
    assert config.remote_port == 2222
    assert config.remote_user == "root"
    assert config.remote_password == "pass"
    assert config.remote_backup_path == "/root/backups"


def test_load_global_config_reads_backup_settings(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "DB_BACKUP_ENABLED=1\n"
        "DB_BACKUP_SCHEDULE=*/10 * * * *\n"
        "DB_BACKUP_FORMAT=.sql\n"
        "DB_BACKUP_TIMEOUT_SECONDS=120\n"
        "DB_BACKUP_MAX_DAYS=30\n"
        "DB_BACKUP_MAX_FILES=14\n",
        encoding="utf-8",
    )

    config = load_global_config(tmp_path)

    assert config.backup_enabled == "1"
    assert config.backup_schedule == "*/10 * * * *"
    assert config.backup_format == ".sql"
    assert config.backup_timeout_seconds == 120
    assert config.backup_max_days == 30
    assert config.backup_max_files == 14


def test_load_global_config_rejects_invalid_remote_port(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "DB_REMOTE_PORT=invalid\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="DB_REMOTE_PORT must be an integer"):
        load_global_config(tmp_path)


def test_load_global_config_rejects_invalid_backup_numeric_settings(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "DB_BACKUP_TIMEOUT_SECONDS=invalid\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="DB_BACKUP_TIMEOUT_SECONDS must be an integer"):
        load_global_config(tmp_path)

    (tmp_path / ".env").write_text(
        "DB_BACKUP_MAX_DAYS=0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="DB_BACKUP_MAX_DAYS must be a positive integer"):
        load_global_config(tmp_path)


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
