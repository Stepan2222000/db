from __future__ import annotations

import logging
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import typer

from ops.core.config import load_service_config
from ops.core.docker import container_is_running
from ops.operations.postgres import (
    query_database_size,
    required_dump_bytes,
    resolve_dump_format,
    stream_pg_dump_to_consumer,
)

LOGGER = logging.getLogger(__name__)


def dump(name: str = typer.Argument(..., metavar="NAME")) -> None:
    project_root = Path.cwd()
    service_config = load_service_config(project_root, name)
    container_name = service_config.name

    if not container_is_running(container_name):
        LOGGER.warning("%s: service container is not running", container_name)
        return

    database_size = query_database_size(container_name, service_config)
    dump_format = resolve_dump_format(service_config)

    dumps_dir = project_root / "dumps"
    dumps_dir.mkdir(parents=True, exist_ok=True)

    required_bytes = required_dump_bytes(database_size, dump_format)
    available_bytes = shutil.disk_usage(dumps_dir).free
    if required_bytes > available_bytes:
        LOGGER.warning(
            "%s: not enough free space for dump (need %d bytes, have %d bytes)",
            container_name,
            required_bytes,
            available_bytes,
        )
        return

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    dump_path = dumps_dir / f"{container_name}_{timestamp}{dump_format}"

    try:
        if dump_format == ".sql":
            _write_plain_dump(container_name, service_config, dump_path)
        else:
            _write_gzip_dump(container_name, service_config, dump_path)
    except Exception:
        dump_path.unlink(missing_ok=True)
        raise

    LOGGER.info("Dump written to %s", dump_path)
    LOGGER.info("Dump size: %d bytes", dump_path.stat().st_size)


def _write_plain_dump(container_name, service_config, dump_path: Path) -> None:
    with dump_path.open("wb") as handle:
        stream_pg_dump_to_consumer(
            container_name,
            service_config,
            ".sql",
            lambda stream: shutil.copyfileobj(stream, handle),
        )


def _write_gzip_dump(container_name, service_config, dump_path: Path) -> None:
    with dump_path.open("wb") as handle:
        stream_pg_dump_to_consumer(
            container_name,
            service_config,
            ".sql.gz",
            lambda stream: shutil.copyfileobj(stream, handle),
        )
