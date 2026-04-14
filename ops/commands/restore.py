from __future__ import annotations

import contextlib
import logging
import socket
from datetime import datetime
from pathlib import Path

import typer

from ops.core.config import load_global_config, load_service_config
from ops.core.docker import container_exists, container_is_running
from ops.core.ssh import build_remote_config, open_remote_session
from ops.operations.postgres import format_size_gb
from ops.operations.restore import (
    build_restore_selection,
    current_database_size,
    download_remote_restore,
    drop_and_recreate_service_database,
    list_local_restore_sources,
    list_remote_restore_sources,
    restore_gzip_file,
    restore_source_from_path,
    restore_sql_file,
    terminate_service_connections,
    validate_restore_extension,
)

LOGGER = logging.getLogger(__name__)


def restore(
    name: str = typer.Argument(..., metavar="NAME"),
    path: str | None = typer.Argument(None, metavar="[PATH]"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    project_root = Path.cwd()
    service_config = load_service_config(project_root, name)
    selected_source = _select_restore_source(project_root, service_config.name, path)
    if selected_source is None:
        return
    temp_download_path: Path | None = None

    try:
        if selected_source.kind == "remote":
            global_config = load_global_config(project_root)
            remote_config = build_remote_config(global_config)
            with open_remote_session(remote_config) as session:
                temp_download_path = download_remote_restore(
                    session,
                    selected_source.remote_path,
                    validate_restore_extension(selected_source.display_name),
                )
            selected_source = selected_source.__class__(
                kind=selected_source.kind,
                display_name=selected_source.display_name,
                path=temp_download_path,
                remote_path=selected_source.remote_path,
                size_bytes=selected_source.size_bytes,
                mtime_epoch=selected_source.mtime_epoch,
                is_temporary_local_copy=True,
            )

        container_name = service_config.name
        if not container_exists(container_name) or not container_is_running(container_name):
            LOGGER.warning("%s: service container is not running; start it first", container_name)
            return

        current_size = current_database_size(container_name, service_config)
        LOGGER.warning("Restore will replace current data:")
        LOGGER.warning("service: %s", service_config.name)
        LOGGER.warning("current size: %s", format_size_gb(current_size))
        LOGGER.warning("source: [%s] %s", selected_source.kind, selected_source.display_name)

        if not force:
            confirmed = typer.confirm("Restore this backup now?", default=False)
            if not confirmed:
                LOGGER.warning("Restore cancelled")
                return

        terminate_service_connections(
            container_name,
            service_config.postgres_user,
            service_config.name,
        )
        drop_and_recreate_service_database(
            container_name,
            service_config.postgres_user,
            service_config.name,
        )

        assert selected_source.path is not None
        restore_suffix = validate_restore_extension(selected_source.path)
        if restore_suffix == ".sql":
            restore_sql_file(
                container_name,
                service_config.postgres_user,
                service_config.name,
                selected_source.path,
            )
        else:
            restore_gzip_file(
                container_name,
                service_config.postgres_user,
                service_config.name,
                selected_source.path,
            )

        LOGGER.info("Restore completed from %s", selected_source.display_name)
    finally:
        if temp_download_path is not None:
            temp_download_path.unlink(missing_ok=True)


def _select_restore_source(project_root: Path, service_name: str, path: str | None):
    if path is not None:
        local_path = Path(path)
        validate_restore_extension(local_path)
        return restore_source_from_path(local_path)

    local_sources = list_local_restore_sources(project_root, service_name)
    global_config = load_global_config(project_root)
    remote_config = build_remote_config(global_config)
    with open_remote_session(remote_config) as session:
        remote_sources = list_remote_restore_sources(
            session,
            remote_config.backup_path,
            socket.gethostname(),
            service_name,
        )
    sources = build_restore_selection(local_sources, remote_sources)
    if not sources:
        LOGGER.info("No restore sources found for %s", service_name)
        return None

    LOGGER.info("Available restore sources:")
    for index, source in enumerate(sources, start=1):
        timestamp = datetime.fromtimestamp(source.mtime_epoch).strftime("%Y-%m-%d %H:%M")
        LOGGER.info(
            "%d. [%s] %s (%s, %d bytes)",
            index,
            source.kind,
            source.display_name,
            timestamp,
            source.size_bytes,
        )

    while True:
        selected = typer.prompt("Select restore source number")
        try:
            selection = int(selected)
        except ValueError:
            LOGGER.warning("Invalid selection: enter a number")
            continue
        if 1 <= selection <= len(sources):
            return sources[selection - 1]
        LOGGER.warning("Invalid selection: choose a listed number")
