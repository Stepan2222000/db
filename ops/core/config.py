from __future__ import annotations

import re
from pathlib import Path

from ops.core.env_files import read_env_file
from ops.core.models import GlobalConfig, ServiceConfig, VALID_BACKUP_FORMATS

SERVICE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")

REQUIRED_SERVICE_KEYS = (
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_PORT",
)
OPTIONAL_SERVICE_KEYS = (
    "POSTGRES_MAX_CONNECTIONS",
    "POSTGRES_BACKUP_DISABLED",
    "POSTGRES_BACKUP_FORMAT",
    "POSTGRES_MEMORY_LIMIT",
    "POSTGRES_CPU_LIMIT",
)
KNOWN_SERVICE_KEYS = set(REQUIRED_SERVICE_KEYS) | set(OPTIONAL_SERVICE_KEYS)


def validate_service_name(service_name: str) -> None:
    if not SERVICE_NAME_PATTERN.fullmatch(service_name):
        raise ValueError(
            f"Invalid service name {service_name!r}: only letters, digits, and underscores are allowed"
        )


def service_env_path(project_root: Path, service_name: str) -> Path:
    validate_service_name(service_name)
    return project_root / "services" / f".env.{service_name}"


def load_global_config(project_root: Path) -> GlobalConfig:
    values = read_env_file(project_root / ".env")
    postgres_version = values.get("DB_POSTGRES_VERSION", "18").strip()
    if not postgres_version:
        raise ValueError("DB_POSTGRES_VERSION cannot be empty")

    default_password = values.get("DB_DEFAULT_POSTGRES_PASSWORD")
    if default_password is not None:
        default_password = default_password.strip()

    remote_port = _parse_optional_port(values.get("DB_REMOTE_PORT"), default=22)

    return GlobalConfig(
        postgres_version=postgres_version,
        default_postgres_password=default_password,
        remote_host=_optional_str(values.get("DB_REMOTE_HOST")),
        remote_port=remote_port,
        remote_user=_optional_str(values.get("DB_REMOTE_USER")),
        remote_password=_optional_str(values.get("DB_REMOTE_PASSWORD")),
        remote_backup_path=_optional_str(values.get("DB_REMOTE_BACKUP_PATH")),
        backup_enabled=_parse_boolish_flag(values.get("DB_BACKUP_ENABLED")),
        backup_schedule=_optional_str(values.get("DB_BACKUP_SCHEDULE")),
        backup_format=_parse_backup_format(values.get("DB_BACKUP_FORMAT"), "DB_BACKUP_FORMAT"),
        backup_timeout_seconds=_parse_global_positive_int(
            values.get("DB_BACKUP_TIMEOUT_SECONDS"),
            "DB_BACKUP_TIMEOUT_SECONDS",
        ),
        backup_max_days=_parse_global_positive_int(
            values.get("DB_BACKUP_MAX_DAYS"),
            "DB_BACKUP_MAX_DAYS",
        ),
        backup_max_files=_parse_global_positive_int(
            values.get("DB_BACKUP_MAX_FILES"),
            "DB_BACKUP_MAX_FILES",
        ),
        metrics_enabled=_parse_boolish_flag(values.get("DB_METRICS_ENABLED")),
        metrics_interval_minutes=_parse_global_positive_int(
            values.get("DB_METRICS_INTERVAL_MINUTES"),
            "DB_METRICS_INTERVAL_MINUTES",
        ),
    )


def load_service_config(project_root: Path, service_name: str) -> ServiceConfig:
    env_path = service_env_path(project_root, service_name)
    values = read_env_file(env_path)

    unknown_keys = sorted(set(values) - KNOWN_SERVICE_KEYS)
    if unknown_keys:
        raise ValueError(
            f"{env_path}: unsupported keys: {', '.join(unknown_keys)}"
        )

    missing_keys = [key for key in REQUIRED_SERVICE_KEYS if key not in values]
    if missing_keys:
        raise ValueError(f"{env_path}: missing required keys: {', '.join(missing_keys)}")

    postgres_user = values["POSTGRES_USER"]
    postgres_password = values["POSTGRES_PASSWORD"]
    if not postgres_user:
        raise ValueError(f"{env_path}: POSTGRES_USER cannot be empty")
    if not postgres_password:
        raise ValueError(f"{env_path}: POSTGRES_PASSWORD cannot be empty")

    postgres_port = _parse_port(values["POSTGRES_PORT"], env_path)
    max_connections = _parse_positive_int(
        values.get("POSTGRES_MAX_CONNECTIONS"),
        env_path,
        "POSTGRES_MAX_CONNECTIONS",
    )

    return ServiceConfig(
        name=service_name,
        env_path=env_path,
        postgres_user=postgres_user,
        postgres_password=postgres_password,
        postgres_port=postgres_port,
        max_connections=max_connections,
        backup_disabled=_parse_boolish_flag(values.get("POSTGRES_BACKUP_DISABLED")),
        backup_format=_parse_backup_format(
            values.get("POSTGRES_BACKUP_FORMAT"),
            "POSTGRES_BACKUP_FORMAT",
        ),
        memory_limit=values.get("POSTGRES_MEMORY_LIMIT"),
        cpu_limit=values.get("POSTGRES_CPU_LIMIT"),
    )


def _parse_port(raw_value: str, env_path: Path) -> int:
    try:
        port = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{env_path}: POSTGRES_PORT must be an integer") from exc

    if not 1024 <= port <= 65535:
        raise ValueError(f"{env_path}: POSTGRES_PORT must be within 1024..65535")

    return port


def _parse_positive_int(
    raw_value: str | None,
    env_path: Path,
    field_name: str,
) -> int | None:
    if raw_value is None:
        return None

    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{env_path}: {field_name} must be an integer") from exc

    if value <= 0:
        raise ValueError(f"{env_path}: {field_name} must be a positive integer")

    return value


def _optional_str(raw_value: str | None) -> str | None:
    if raw_value is None:
        return None

    value = raw_value.strip()
    return value or None


def _parse_optional_port(raw_value: str | None, *, default: int) -> int:
    if raw_value is None:
        return default

    value = raw_value.strip()
    if not value:
        return default

    try:
        port = int(value)
    except ValueError as exc:
        raise ValueError("DB_REMOTE_PORT must be an integer") from exc

    if not 1 <= port <= 65535:
        raise ValueError("DB_REMOTE_PORT must be within 1..65535")

    return port


def _parse_global_positive_int(raw_value: str | None, field_name: str) -> int | None:
    if raw_value is None:
        return None

    value = raw_value.strip()
    if not value:
        return None

    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an integer") from exc

    if parsed <= 0:
        raise ValueError(f"{field_name} must be a positive integer")

    return parsed


def _parse_boolish_flag(raw_value: str | None) -> bool:
    if raw_value is None:
        return False
    return raw_value.strip().lower() not in {"", "0", "false", "no"}


def _parse_backup_format(raw_value: str | None, field_name: str) -> str | None:
    value = _optional_str(raw_value)
    if value is None:
        return None
    if value not in VALID_BACKUP_FORMATS:
        raise ValueError(f"{field_name} must be one of .sql, .sql.gz")
    return value
