from __future__ import annotations

import contextlib
import math
import subprocess
import time
from typing import BinaryIO, Callable, TypeVar

from ops.core.docker import docker_exec_capture, docker_exec_popen
from ops.core.models import ServiceConfig, VALID_BACKUP_FORMATS

T = TypeVar("T")


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
    command = build_psql_argv(
        postgres_user,
        database,
        tuples_only=tuples_only,
        no_align=no_align,
    )
    if not stdin_sql:
        command.extend(["-c", sql])

    process = psql_popen(
        container_name,
        postgres_user,
        database,
        stdin=subprocess.PIPE if stdin_sql else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        interactive=stdin_sql,
        tuples_only=tuples_only,
        no_align=no_align,
        extra_argv=[] if stdin_sql else ["-c", sql],
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


def resolve_dump_format(
    service_config: ServiceConfig,
    global_backup_format: str | None = None,
) -> str:
    raw_format = service_config.backup_format
    if raw_format is None:
        raw_format = global_backup_format
    if raw_format is None:
        return ".sql.gz"

    dump_format = raw_format.strip()
    if dump_format not in VALID_BACKUP_FORMATS:
        field_name = (
            "POSTGRES_BACKUP_FORMAT"
            if service_config.backup_format is not None
            else "DB_BACKUP_FORMAT"
        )
        raise ValueError(f"{field_name} must be one of .sql, .sql.gz")
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


def build_psql_argv(
    postgres_user: str,
    database: str,
    *,
    tuples_only: bool = False,
    no_align: bool = False,
    extra_argv: list[str] | None = None,
) -> list[str]:
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
    if extra_argv:
        command.extend(extra_argv)
    return command


def psql_popen(
    container_name: str,
    postgres_user: str,
    database: str,
    *,
    stdin: int | BinaryIO | None,
    stdout: int | BinaryIO | None,
    stderr: int | BinaryIO | None,
    interactive: bool,
    tuples_only: bool = False,
    no_align: bool = False,
    extra_argv: list[str] | None = None,
) -> subprocess.Popen[bytes]:
    return docker_exec_popen(
        container_name,
        build_psql_argv(
            postgres_user,
            database,
            tuples_only=tuples_only,
            no_align=no_align,
            extra_argv=extra_argv,
        ),
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        interactive=interactive,
    )


def stream_pg_dump_to_consumer(
    container_name: str,
    service_config: ServiceConfig,
    dump_format: str,
    consumer: Callable[[BinaryIO], T],
    *,
    timeout_seconds: float | None = None,
) -> T:
    if dump_format == ".sql":
        dump_process = pg_dump_popen(
            container_name,
            service_config,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert dump_process.stdout is not None
        try:
            result = consumer(dump_process.stdout)
            dump_process.stdout.close()
            dump_returncode = dump_process.wait(timeout=timeout_seconds)
            dump_stderr = (
                dump_process.stderr.read().decode("utf-8", "replace")
                if dump_process.stderr is not None
                else ""
            )
        except Exception:
            _terminate_process(dump_process)
            raise
        finally:
            if dump_process.stdout is not None:
                dump_process.stdout.close()

        if dump_returncode != 0:
            raise RuntimeError(f"{container_name}: pg_dump failed: {dump_stderr}")
        return result

    if dump_format == ".sql.gz":
        dump_process = pg_dump_popen(
            container_name,
            service_config,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert dump_process.stdout is not None
        gzip_process = subprocess.Popen(
            ["gzip", "-c"],
            stdin=dump_process.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert gzip_process.stdout is not None
        dump_process.stdout.close()
        try:
            result = consumer(gzip_process.stdout)
            gzip_process.stdout.close()
            gzip_returncode = gzip_process.wait(timeout=timeout_seconds)
            dump_returncode = dump_process.wait(timeout=timeout_seconds)
            gzip_stderr = (
                gzip_process.stderr.read().decode("utf-8", "replace")
                if gzip_process.stderr is not None
                else ""
            )
            dump_stderr = (
                dump_process.stderr.read().decode("utf-8", "replace")
                if dump_process.stderr is not None
                else ""
            )
        except Exception:
            _terminate_process(gzip_process)
            _terminate_process(dump_process)
            raise
        finally:
            if gzip_process.stdout is not None:
                gzip_process.stdout.close()

        if dump_returncode != 0:
            raise RuntimeError(f"{container_name}: pg_dump failed: {dump_stderr}")
        if gzip_returncode != 0:
            raise RuntimeError(f"{container_name}: gzip failed: {gzip_stderr}")
        return result

    raise ValueError(f"Unsupported dump format: {dump_format}")


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(OSError, ProcessLookupError):
        process.kill()
    with contextlib.suppress(Exception):
        process.wait(timeout=5)


def format_size_gb(size_bytes: int) -> str:
    size_gb = size_bytes / (1024 ** 3)
    return f"{size_gb:.2f} GB"


def sql_literal(value: str) -> str:
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def sql_identifier(value: str) -> str:
    escaped = value.replace('"', '""')
    return f'"{escaped}"'
