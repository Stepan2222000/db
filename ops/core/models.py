from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class GlobalConfig:
    postgres_version: str
    default_postgres_password: str | None
    remote_host: str | None = None
    remote_port: int = 22
    remote_user: str | None = None
    remote_password: str | None = None
    remote_backup_path: str | None = None
    backup_enabled: str | None = None
    backup_schedule: str | None = None
    backup_format: str | None = None
    backup_timeout_seconds: int | None = None
    backup_max_days: int | None = None
    backup_max_files: int | None = None
    metrics_enabled: str | None = None
    metrics_interval_minutes: int | None = None


@dataclass(frozen=True, slots=True)
class ServiceConfig:
    name: str
    env_path: Path
    postgres_user: str
    postgres_password: str
    postgres_port: int
    max_connections: int | None = None
    backup_disabled: str | None = None
    backup_format: str | None = None
    memory_limit: str | None = None
    cpu_limit: str | None = None
