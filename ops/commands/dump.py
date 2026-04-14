from __future__ import annotations

import logging
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import typer

from ops.core.config import load_service_config
from ops.core.docker import container_exists, container_is_running
from ops.operations.postgres import (
    pg_dump_popen,
    query_database_size,
    required_dump_bytes,
    resolve_dump_format,
)

LOGGER = logging.getLogger(__name__)


def dump(name: str = typer.Argument(..., metavar="NAME")) -> None:
    project_root = Path.cwd()
    service_config = load_service_config(project_root, name)
    container_name = service_config.name

    if not container_exists(container_name) or not container_is_running(container_name):
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
        process = pg_dump_popen(
            container_name,
            service_config,
            stdout=handle,
            stderr=subprocess.PIPE,
        )
        _, stderr_bytes = process.communicate()

    if process.returncode != 0:
        raise subprocess.CalledProcessError(
            process.returncode,
            ["docker", "exec", container_name, "pg_dump"],
            stderr=(stderr_bytes or b"").decode("utf-8", "replace"),
        )


def _write_gzip_dump(container_name, service_config, dump_path: Path) -> None:
    with dump_path.open("wb") as handle:
        dump_process = pg_dump_popen(
            container_name,
            service_config,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            gzip_process = subprocess.Popen(
                ["gzip", "-c"],
                stdin=dump_process.stdout,
                stdout=handle,
                stderr=subprocess.PIPE,
            )
            assert dump_process.stdout is not None
            dump_process.stdout.close()
            _, gzip_stderr = gzip_process.communicate()
            dump_returncode = dump_process.wait()
            dump_stderr = (
                dump_process.stderr.read() if dump_process.stderr is not None else b""
            )
        finally:
            if dump_process.stdout is not None:
                dump_process.stdout.close()

    if dump_returncode != 0:
        raise subprocess.CalledProcessError(
            dump_returncode,
            ["docker", "exec", container_name, "pg_dump"],
            stderr=dump_stderr.decode("utf-8", "replace"),
        )
    if gzip_process.returncode != 0:
        raise subprocess.CalledProcessError(
            gzip_process.returncode,
            ["gzip", "-c"],
            stderr=(gzip_stderr or b"").decode("utf-8", "replace"),
        )
