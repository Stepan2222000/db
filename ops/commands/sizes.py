from __future__ import annotations

import logging
from pathlib import Path

from ops.core.docker import container_is_running
from ops.operations.postgres import format_size_gb, query_database_size
from ops.operations.services import load_all_service_configs

LOGGER = logging.getLogger(__name__)


def sizes() -> None:
    project_root = Path.cwd()
    service_configs = load_all_service_configs(project_root)
    if not service_configs:
        LOGGER.info("No services found")
        return

    for service_config in service_configs:
        if not container_is_running(service_config.name):
            LOGGER.info("%s: stopped, start the service first", service_config.name)
            continue

        size_bytes = query_database_size(service_config.name, service_config)
        LOGGER.info("%s: %s", service_config.name, format_size_gb(size_bytes))
