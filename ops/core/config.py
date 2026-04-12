"""Project configuration loading and validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .discovery import discover_service_env_files, service_name_from_path
from .env_files import EnvFileError, read_env_file


class ConfigError(ValueError):
    """Raised when the project configuration does not satisfy Stage 1 rules."""


@dataclass(frozen=True)
class GlobalConfig:
    """Глобальная конфигурация проекта из корневого ``.env``.

    Attrs:
        postgres_version: версия PostgreSQL (сейчас поддерживается только ``"18"``).
        raw: все переменные из ``.env`` в сыром виде.
    """
    postgres_version: str
    raw: Mapping[str, str]


@dataclass(frozen=True)
class ServiceConfig:
    """Конфигурация одного PostgreSQL-сервиса из ``services/.env.<имя>``.

    Attrs:
        name: имя сервиса (из имени файла).
        path: путь к env-файлу сервиса.
        raw: все переменные из файла в сыром виде.
        postgres_user: пользователь БД (обязательный).
        postgres_password: пароль БД (обязательный).
        postgres_port: порт для проброса наружу (1–65535, обязательный).
        postgres_max_connections: лимит соединений (опциональный).
        postgres_memory_limit: лимит памяти контейнера, например ``"1G"`` (опциональный).
        postgres_cpu_limit: лимит CPU контейнера, например ``"0.5"`` (опциональный).
    """
    name: str
    path: Path
    raw: Mapping[str, str]
    postgres_user: str
    postgres_password: str
    postgres_port: int
    postgres_max_connections: int | None
    postgres_memory_limit: str | None
    postgres_cpu_limit: str | None


def load_global_config(project_dir: Path) -> GlobalConfig:
    """Загружает глобальную конфигурацию из ``<project_dir>/.env``.

    Требует наличие ключа ``POSTGRES_VERSION=18``.

    Args:
        project_dir: корневой каталог проекта.

    Returns:
        GlobalConfig с версией PostgreSQL и сырыми значениями.

    Raises:
        ConfigError: если файл не найден, версия отсутствует или не равна ``"18"``.
    """
    global_env_path = project_dir / ".env"
    try:
        parsed = read_env_file(global_env_path)
    except EnvFileError as exc:
        raise ConfigError(str(exc)) from exc

    version = parsed.values.get("POSTGRES_VERSION")
    if version is None:
        raise ConfigError(f"Missing required key POSTGRES_VERSION in {global_env_path}")
    if version != "18":
        raise ConfigError(
            f"Unsupported POSTGRES_VERSION={version!r} in {global_env_path}; "
            "Stage 1 supports only PostgreSQL 18"
        )

    return GlobalConfig(postgres_version=version, raw=dict(parsed.values))


def load_service_configs(project_dir: Path) -> list[ServiceConfig]:
    """Загружает конфигурации всех сервисов из ``services/.env.*``.

    Args:
        project_dir: корневой каталог проекта.

    Returns:
        Список ServiceConfig для каждого обнаруженного сервиса.

    Raises:
        ConfigError: при ошибках чтения или валидации env-файлов.
    """
    configs: list[ServiceConfig] = []
    for path in discover_service_env_files(project_dir):
        try:
            parsed = read_env_file(path)
        except EnvFileError as exc:
            raise ConfigError(str(exc)) from exc
        configs.append(_build_service_config(path, parsed.values))
    return configs


def _build_service_config(path: Path, values: Mapping[str, str]) -> ServiceConfig:
    """Собирает ServiceConfig из сырых значений env-файла.

    Извлекает и валидирует обязательные и опциональные поля.

    Args:
        path: путь к env-файлу сервиса.
        values: словарь переменных из файла.

    Returns:
        Готовый ServiceConfig.

    Raises:
        ConfigError: при отсутствии обязательных полей или невалидных значениях.
    """
    service_name = service_name_from_path(path)

    postgres_user = _require_string(values, "POSTGRES_USER", path)
    postgres_password = _require_string(values, "POSTGRES_PASSWORD", path)
    postgres_port = _require_int(values, "POSTGRES_PORT", path, minimum=1, maximum=65535)
    postgres_max_connections = _optional_int(
        values,
        "POSTGRES_MAX_CONNECTIONS",
        path,
        minimum=1,
    )
    postgres_memory_limit = _optional_string(values, "POSTGRES_MEMORY_LIMIT")
    postgres_cpu_limit = _optional_string(values, "POSTGRES_CPU_LIMIT")

    return ServiceConfig(
        name=service_name,
        path=path,
        raw=dict(values),
        postgres_user=postgres_user,
        postgres_password=postgres_password,
        postgres_port=postgres_port,
        postgres_max_connections=postgres_max_connections,
        postgres_memory_limit=postgres_memory_limit,
        postgres_cpu_limit=postgres_cpu_limit,
    )


def _require_string(values: Mapping[str, str], key: str, path: Path) -> str:
    """Извлекает обязательную строковую переменную. Ошибка если отсутствует или пустая."""
    value = values.get(key)
    if value is None or value == "":
        raise ConfigError(f"Missing required key {key} in {path}")
    return value


def _optional_string(values: Mapping[str, str], key: str) -> str | None:
    """Извлекает опциональную строковую переменную. Возвращает None если отсутствует."""
    value = values.get(key)
    if value is None or value == "":
        return None
    return value


def _require_int(
    values: Mapping[str, str],
    key: str,
    path: Path,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Извлекает обязательную целочисленную переменную с проверкой границ."""
    value = _require_string(values, key, path)
    return _parse_int(value, key, path, minimum=minimum, maximum=maximum)


def _optional_int(
    values: Mapping[str, str],
    key: str,
    path: Path,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    """Извлекает опциональную целочисленную переменную. None если отсутствует."""
    value = values.get(key)
    if value is None or value == "":
        return None
    return _parse_int(value, key, path, minimum=minimum, maximum=maximum)


def _parse_int(
    raw_value: str,
    key: str,
    path: Path,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Парсит строку в int с проверкой диапазона [minimum, maximum].

    Args:
        raw_value: строковое значение для парсинга.
        key: имя переменной (для сообщений об ошибках).
        path: путь к файлу (для сообщений об ошибках).
        minimum: нижняя граница (включительно), None — без ограничения.
        maximum: верхняя граница (включительно), None — без ограничения.

    Raises:
        ConfigError: если значение не число или выходит за границы.
    """
    try:
        parsed = int(raw_value)
    except ValueError as exc:
        raise ConfigError(f"Key {key} in {path} must be an integer, got {raw_value!r}") from exc

    if minimum is not None and parsed < minimum:
        raise ConfigError(f"Key {key} in {path} must be >= {minimum}, got {parsed}")
    if maximum is not None and parsed > maximum:
        raise ConfigError(f"Key {key} in {path} must be <= {maximum}, got {parsed}")
    return parsed

