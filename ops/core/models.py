from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class GlobalConfig:
    postgres_version: str
    default_postgres_password: str | None


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
