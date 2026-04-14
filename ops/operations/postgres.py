from __future__ import annotations

import math
import subprocess
import time
from typing import BinaryIO

from ops.core.docker import docker_exec_capture, docker_exec_popen
from ops.core.models import ServiceConfig

VALID_DUMP_FORMATS = frozenset({".sql", ".sql.gz"})


def wait_for_pg_ready(
    container_name: str,
    service_name: str,
    postgres_user: str,
    timeout_seconds: float = 60.0,
    poll_interval: float = 1.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds

    while True:
        result = docker_exec_capture(
            container_name,
            [
                "pg_isready",
                "-q",
                "-U",
                postgres_user,
                "-d",
                service_name,
            ],
            check=False,
        )
        if result.returncode == 0:
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"{container_name}: PostgreSQL did not become ready within {timeout_seconds} seconds"
            )
        time.sleep(poll_interval)


def run_psql(
    container_name: str,
    postgres_user: str,
    database: str,
    sql: str,
    *,
    stdin_sql: bool = False,
    tuples_only: bool = False,
    no_align: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = [
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "--username",
        postgres_user,
        "--dbname",
        database,
    ]
    if tuples_only:
        command.append("--tuples-only")
    if no_align:
        command.append("--no-align")
    if not stdin_sql:
        command.extend(["-c", sql])

    process = docker_exec_popen(
        container_name,
        command,
        stdin=subprocess.PIPE if stdin_sql else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        interactive=stdin_sql,
    )
    stdout_bytes, stderr_bytes = process.communicate(
        sql.encode("utf-8") if stdin_sql else None
    )
    stdout = stdout_bytes.decode("utf-8")
    stderr = stderr_bytes.decode("utf-8")
    if process.returncode != 0:
        raise subprocess.CalledProcessError(
            process.returncode,
            ["docker", "exec", container_name, *command],
            output=stdout,
            stderr=stderr,
        )
    return subprocess.CompletedProcess(
        ["docker", "exec", container_name, *command],
        process.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def sync_service_password(
    container_name: str,
    service_config: ServiceConfig,
) -> None:
    sql = (
        f"ALTER ROLE {sql_identifier(service_config.postgres_user)} "
        f"WITH PASSWORD {sql_literal(service_config.postgres_password)};\n"
    )
    run_psql(
        container_name,
        service_config.postgres_user,
        "postgres",
        sql,
        stdin_sql=True,
    )


def query_database_size(
    container_name: str,
    service_config: ServiceConfig,
) -> int:
    result = run_psql(
        container_name,
        service_config.postgres_user,
        service_config.name,
        "SELECT pg_database_size(current_database());",
        tuples_only=True,
        no_align=True,
    )
    value = result.stdout.strip()
    if not value:
        raise ValueError(
            f"{container_name}: pg_database_size returned an empty result"
        )
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(
            f"{container_name}: pg_database_size returned a non-integer value: {value!r}"
        ) from exc


def resolve_dump_format(service_config: ServiceConfig) -> str:
    if service_config.backup_format is None:
        return ".sql.gz"

    dump_format = service_config.backup_format.strip()
    if dump_format not in VALID_DUMP_FORMATS:
        raise ValueError(
            f"{service_config.env_path}: POSTGRES_BACKUP_FORMAT must be one of .sql, .sql.gz"
        )
    return dump_format


def required_dump_bytes(size_bytes: int, dump_format: str) -> int:
    if dump_format == ".sql":
        return size_bytes
    if dump_format == ".sql.gz":
        return math.ceil(size_bytes * 0.3)
    raise ValueError(f"Unsupported dump format: {dump_format}")


def pg_dump_popen(
    container_name: str,
    service_config: ServiceConfig,
    *,
    stdout: int | BinaryIO | None,
    stderr: int | BinaryIO | None,
) -> subprocess.Popen[bytes]:
    return docker_exec_popen(
        container_name,
        [
            "pg_dump",
            "-U",
            service_config.postgres_user,
            "-d",
            service_config.name,
        ],
        stdout=stdout,
        stderr=stderr,
    )


def format_size_gb(size_bytes: int) -> str:
    size_gb = size_bytes / (1024 ** 3)
    return f"{size_gb:.2f} GB"


def sql_literal(value: str) -> str:
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def sql_identifier(value: str) -> str:
    escaped = value.replace('"', '""')
    return f'"{escaped}"'
