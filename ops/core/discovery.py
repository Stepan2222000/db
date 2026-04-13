from __future__ import annotations

from pathlib import Path

from ops.core.config import validate_service_name


def discover_services(project_root: Path) -> list[str]:
    service_names: list[str] = []

    for env_path in sorted((project_root / "services").glob(".env.*")):
        service_name = env_path.name.removeprefix(".env.")
        validate_service_name(service_name)
        service_names.append(service_name)

    return service_names
