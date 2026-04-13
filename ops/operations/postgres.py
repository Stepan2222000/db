from __future__ import annotations

import subprocess
import time

from ops.core.docker import docker_exec_capture, docker_exec_popen
from ops.core.models import ServiceConfig


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


def sql_literal(value: str) -> str:
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def sql_identifier(value: str) -> str:
    escaped = value.replace('"', '""')
    return f'"{escaped}"'
