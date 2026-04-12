"""Service env-file discovery helpers."""

from __future__ import annotations

from pathlib import Path
import re

SERVICE_DIRNAME = "services"  # Имя каталога с env-файлами сервисов
SERVICE_FILE_PREFIX = ".env."  # Префикс файлов конфигурации сервисов (например .env.mydb)
SERVICE_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")  # Допустимые символы в имени сервиса


class ServiceDiscoveryError(ValueError):
    """Raised when service discovery finds an invalid state."""


def service_name_from_path(path: Path) -> str:
    """Извлекает имя сервиса из пути к env-файлу.

    Из файла ``services/.env.mydb`` вернёт ``"mydb"``.

    Args:
        path: путь к файлу вида ``.env.<имя_сервиса>``.

    Returns:
        Имя сервиса (строка из букв, цифр и ``_``).

    Raises:
        ServiceDiscoveryError: если имя файла не соответствует формату.
    """
    filename = path.name
    if not filename.startswith(SERVICE_FILE_PREFIX):
        raise ServiceDiscoveryError(f"Unsupported service env filename: {path}")

    service_name = filename[len(SERVICE_FILE_PREFIX) :]
    if not service_name:
        raise ServiceDiscoveryError(f"Service env filename has no service name: {path}")
    if not SERVICE_NAME_RE.fullmatch(service_name):
        raise ServiceDiscoveryError(f"Invalid service name '{service_name}' in {path}")

    return service_name


def discover_service_env_files(project_dir: Path) -> list[Path]:
    """Находит все env-файлы сервисов в каталоге ``services/``.

    Сканирует ``<project_dir>/services/`` и возвращает отсортированный список
    путей к файлам ``.env.*``. Проверяет уникальность имён сервисов.

    Args:
        project_dir: корневой каталог проекта.

    Returns:
        Отсортированный список путей к env-файлам. Пустой список, если
        каталог ``services/`` отсутствует.

    Raises:
        ServiceDiscoveryError: при дубликатах имён или невалидных путях.
    """
    services_dir = project_dir / SERVICE_DIRNAME
    if not services_dir.exists():
        return []
    if not services_dir.is_dir():
        raise ServiceDiscoveryError(f"services path is not a directory: {services_dir}")

    discovered: dict[str, Path] = {}
    for path in sorted(services_dir.glob(".env.*")):
        if not path.is_file():
            continue

        service_name = service_name_from_path(path)
        if service_name in discovered:
            raise ServiceDiscoveryError(
                f"Duplicate service env detected for '{service_name}': "
                f"{discovered[service_name]} and {path}"
            )
        discovered[service_name] = path

    return [discovered[name] for name in sorted(discovered)]

