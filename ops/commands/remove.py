from __future__ import annotations

import logging
import shutil
from pathlib import Path

import typer

from ops.core.config import service_env_path
from ops.core.discovery import discover_services
from ops.core.docker import container_exists, docker_rm_force
from ops.operations.services import (
    regenerate_compose_for_current_services,
    summarize_remove_target,
)

LOGGER = logging.getLogger(__name__)


def remove(
    name: str = typer.Argument(..., metavar="NAME"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    project_root = Path.cwd()
    env_path = service_env_path(project_root, name)
    if not env_path.exists():
        service_names = discover_services(project_root)
        LOGGER.warning("Service %s was not found", name)
        if service_names:
            LOGGER.info("Existing services: %s", ", ".join(service_names))
        else:
            LOGGER.info("Existing services: none")
        return

    LOGGER.info("Remove target:")
    for line in summarize_remove_target(project_root, name):
        LOGGER.info(line)

    if not force:
        confirmed = typer.confirm("Remove this service?", default=False)
        if not confirmed:
            LOGGER.warning("Removal cancelled")
            return

        repeated_name = typer.prompt("Type the exact service name to confirm")
        if repeated_name != name:
            LOGGER.warning("Removal cancelled: service name mismatch")
            return

        delete_word = typer.prompt("Type DELETE to confirm data removal")
        if delete_word != "DELETE":
            LOGGER.warning("Removal cancelled: expected DELETE")
            return

    if container_exists(name):
        docker_rm_force(name)

    env_path.unlink()

    data_dir = project_root / "data" / name
    if data_dir.exists():
        shutil.rmtree(data_dir)

    compose_path = regenerate_compose_for_current_services(project_root)
    LOGGER.info("Removed service %s", name)
    LOGGER.info("Wrote %s", compose_path)
