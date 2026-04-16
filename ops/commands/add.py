from __future__ import annotations

import logging
from pathlib import Path

import typer

from ops.core.config import load_global_config, load_service_config, service_env_path
from ops.core.docker import compose_up
from ops.core.network import detect_server_host
from ops.operations.postgres import wait_for_pg_ready
from ops.operations.services import (
    create_service_env,
    ensure_service_data_dir,
    generate_compose,
    load_all_service_configs,
    resolve_add_password,
    pick_service_port,
)

LOGGER = logging.getLogger(__name__)


def add(
    name: str = typer.Argument(..., metavar="NAME"),
    port: int | None = typer.Option(None, "--port"),
    password: str | None = typer.Option(None, "--password"),
) -> None:
    project_root = Path.cwd()
    global_config = load_global_config(project_root)
    existing_service_configs = load_all_service_configs(project_root)
    env_path = service_env_path(project_root, name)
    if env_path.exists():
        raise ValueError(f"{env_path}: service configuration already exists")

    resolved_password = resolve_add_password(global_config, password)
    resolved_port = pick_service_port(existing_service_configs, port)

    create_service_env(project_root, name, resolved_password, resolved_port)
    ensure_service_data_dir(project_root, name)

    generate_compose(project_root)

    created_service_config = load_service_config(project_root, name)

    LOGGER.info("Prepared new service:")
    LOGGER.info("name: %s", created_service_config.name)
    LOGGER.info("port: %s", created_service_config.postgres_port)
    LOGGER.info("user: %s", created_service_config.postgres_user)
    LOGGER.info("password: %s", created_service_config.postgres_password)
    LOGGER.info("postgres version: %s", global_config.postgres_version)

    confirmed = typer.confirm("Start the new service now?", default=False)
    if not confirmed:
        LOGGER.warning(
            "Start cancelled; prepared files were left in place and runtime was not changed"
        )
        return

    compose_up(project_root, name)
    wait_for_pg_ready(
        name,
        created_service_config.name,
        created_service_config.postgres_user,
        timeout_seconds=60.0,
        poll_interval=1.0,
    )

    LOGGER.info("Connection parameters:")
    LOGGER.info("host: %s", detect_server_host())
    LOGGER.info("port: %s", created_service_config.postgres_port)
    LOGGER.info("database: %s", created_service_config.name)
    LOGGER.info("user: %s", created_service_config.postgres_user)
    LOGGER.info("password: %s", created_service_config.postgres_password)
