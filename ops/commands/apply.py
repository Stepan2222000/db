from __future__ import annotations

import logging
from pathlib import Path

import typer

from ops.core.docker import container_is_running, compose_up
from ops.operations.postgres import sync_service_password, wait_for_pg_ready
from ops.operations.services import (
    compose_diff_lines,
    ensure_service_data_dir,
    load_all_service_configs,
    regenerate_compose_with_previous_state,
    summarize_apply_changes,
)

LOGGER = logging.getLogger(__name__)


def apply() -> None:
    project_root = Path.cwd()
    desired_service_configs = load_all_service_configs(project_root)
    if not desired_service_configs:
        raise ValueError(
            f"{project_root / 'services'}: no service configuration files found"
        )

    created_data_dirs: set[str] = set()
    for service_config in desired_service_configs:
        _, created = ensure_service_data_dir(project_root, service_config.name)
        if created:
            created_data_dirs.add(service_config.name)

    _, previous_compose_text = regenerate_compose_with_previous_state(project_root)
    summary_lines = summarize_apply_changes(
        project_root,
        desired_service_configs,
        previous_compose_text,
        created_data_dirs=created_data_dirs,
    )
    diff_lines = compose_diff_lines(project_root, previous_compose_text)

    LOGGER.info("Pending apply changes:")
    for line in summary_lines:
        LOGGER.info(line)

    if diff_lines:
        LOGGER.info("Compose diff:")
        for line in diff_lines:
            LOGGER.info(line)
    else:
        LOGGER.info("Compose diff: no textual changes")

    confirmed = typer.confirm("Apply runtime changes now?", default=False)
    if not confirmed:
        LOGGER.warning(
            "Apply cancelled; prepared files were left in place and runtime was not changed"
        )
        return

    compose_up(project_root)

    for service_config in desired_service_configs:
        if not container_is_running(service_config.name):
            continue
        wait_for_pg_ready(
            service_config.name,
            service_config.name,
            service_config.postgres_user,
            timeout_seconds=60.0,
            poll_interval=1.0,
        )
        sync_service_password(service_config.name, service_config)
