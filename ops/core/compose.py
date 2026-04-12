"""Generation of compose.yaml from the project env files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .config import GlobalConfig, ServiceConfig, load_global_config, load_service_configs
from .system import compute_shm_size_bytes, read_memtotal_bytes

# Комментарий-заголовок, который вставляется в начало сгенерированного compose.yaml
GENERATED_COMMENT = "# Автоматически сгенерировано. Не редактировать. Используй db apply."
# Путь к данным PostgreSQL внутри контейнера
PGDATA_TARGET = "/var/lib/postgresql/18/docker"


def generate_compose_text(project_dir: Path, *, proc_meminfo_path: Path = Path("/proc/meminfo")) -> str:
    """Генерирует полный текст compose.yaml на основе env-файлов проекта.

    Загружает глобальную и сервисные конфигурации, строит документ
    Docker Compose и рендерит его в YAML с заголовком-комментарием.

    Args:
        project_dir: корневой каталог проекта.
        proc_meminfo_path: путь к meminfo для расчёта shm_size.

    Returns:
        Текст compose.yaml, готовый к записи на диск.
    """
    global_config = load_global_config(project_dir)
    service_configs = load_service_configs(project_dir)
    document = build_compose_document(
        global_config=global_config,
        service_configs=service_configs,
        proc_meminfo_path=proc_meminfo_path,
    )
    rendered_yaml = yaml.safe_dump(
        document,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    return f"{GENERATED_COMMENT}\n{rendered_yaml}"


def write_compose_file(project_dir: Path, *, proc_meminfo_path: Path = Path("/proc/meminfo")) -> Path:
    """Генерирует и записывает ``compose.yaml`` в корень проекта.

    Args:
        project_dir: корневой каталог проекта.
        proc_meminfo_path: путь к meminfo для расчёта shm_size.

    Returns:
        Путь к записанному файлу ``compose.yaml``.
    """
    compose_text = generate_compose_text(project_dir, proc_meminfo_path=proc_meminfo_path)
    compose_path = project_dir / "compose.yaml"
    compose_path.write_text(compose_text, encoding="utf-8")
    return compose_path


def build_compose_document(
    *,
    global_config: GlobalConfig,
    service_configs: list[ServiceConfig],
    proc_meminfo_path: Path,
) -> dict[str, Any]:
    """Строит словарь Docker Compose документа из конфигураций.

    Для каждого сервиса создаёт секцию с образом PostgreSQL, пробросом порта,
    монтированием тома данных, healthcheck и вычисленным shm_size.
    Опционально добавляет max_connections, лимиты памяти и CPU.

    Args:
        global_config: глобальная конфигурация (версия PostgreSQL).
        service_configs: список конфигураций сервисов.
        proc_meminfo_path: путь к meminfo для расчёта shm_size.

    Returns:
        Словарь ``{"services": {...}}`` для сериализации в YAML.
    """
    services: dict[str, Any] = {}
    shm_size_bytes = None
    if service_configs:
        shm_size_bytes = compute_shm_size_bytes(
            read_memtotal_bytes(proc_meminfo_path),
            len(service_configs),
        )

    for service in sorted(service_configs, key=lambda item: item.name):
        service_document: dict[str, Any] = {
            "image": f"postgres:{global_config.postgres_version}",
            "container_name": service.name,
            "env_file": [
                {
                    "path": f"./services/.env.{service.name}",
                    "format": "raw",
                }
            ],
            "environment": {
                "POSTGRES_DB": service.name,
            },
            "ports": [f"{service.postgres_port}:5432"],
            "volumes": [f"./data/{service.name}:{PGDATA_TARGET}"],
            "restart": "unless-stopped",
            "healthcheck": {
                "test": ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"],
                "interval": "10s",
                "timeout": "5s",
                "retries": 3,
                "start_period": "30s",
            },
            "shm_size": str(shm_size_bytes),
        }

        if service.postgres_max_connections is not None:
            service_document["command"] = [
                "postgres",
                "-c",
                f"max_connections={service.postgres_max_connections}",
            ]

        limits: dict[str, Any] = {}
        if service.postgres_memory_limit is not None:
            limits["memory"] = service.postgres_memory_limit
        if service.postgres_cpu_limit is not None:
            limits["cpus"] = service.postgres_cpu_limit
        if limits:
            service_document["deploy"] = {
                "resources": {
                    "limits": limits,
                }
            }

        services[service.name] = service_document

    return {"services": services}

