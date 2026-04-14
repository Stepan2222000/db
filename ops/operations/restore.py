from __future__ import annotations

import os
import errno
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ops.core.docker import docker_exec_popen
from ops.core.ssh import RemoteSession
from ops.operations.backup import remote_backup_dir
from ops.operations.postgres import query_database_size, run_psql, sql_identifier, sql_literal

VALID_RESTORE_SUFFIXES = (".sql", ".sql.gz")


@dataclass(frozen=True, slots=True)
class RestoreSource:
    kind: Literal["local", "remote", "path"]
    display_name: str
    path: Path | None
    remote_path: str | None
    size_bytes: int
    mtime_epoch: int
    is_temporary_local_copy: bool


def validate_restore_extension(path_or_name: str | Path) -> str:
    value = str(path_or_name)
    if value.endswith(".sql.gz"):
        return ".sql.gz"
    if value.endswith(".sql"):
        return ".sql"
    raise ValueError("Restore source must end with .sql or .sql.gz")


def list_local_restore_sources(project_root: Path, service_name: str) -> list[RestoreSource]:
    dumps_dir = project_root / "dumps"
    if not dumps_dir.exists():
        return []

    sources: list[RestoreSource] = []
    for path in dumps_dir.iterdir():
        if not path.is_file():
            continue
        if not path.name.startswith(f"{service_name}_"):
            continue
        try:
            validate_restore_extension(path.name)
        except ValueError:
            continue
        stat = path.stat()
        sources.append(
            RestoreSource(
                kind="local",
                display_name=path.name,
                path=path,
                remote_path=None,
                size_bytes=stat.st_size,
                mtime_epoch=int(stat.st_mtime),
                is_temporary_local_copy=False,
            )
        )
    return sources


def list_remote_restore_sources(
    session: RemoteSession,
    remote_root: str,
    hostname: str,
    service_name: str,
) -> list[RestoreSource]:
    remote_dir = remote_backup_dir(remote_root, hostname, service_name)
    try:
        entries = session.list_dir(remote_dir)
    except OSError as exc:
        if getattr(exc, "errno", None) == errno.ENOENT or "No such file" in str(exc):
            return []
        raise

    return [
        RestoreSource(
            kind="remote",
            display_name=entry.filename,
            path=None,
            remote_path=f"{remote_dir}/{entry.filename}",
            size_bytes=entry.size_bytes,
            mtime_epoch=entry.mtime_epoch,
            is_temporary_local_copy=False,
        )
        for entry in entries
        if entry.filename.endswith(VALID_RESTORE_SUFFIXES)
    ]


def build_restore_selection(
    local_sources: list[RestoreSource],
    remote_sources: list[RestoreSource],
) -> list[RestoreSource]:
    return sorted(
        [*local_sources, *remote_sources],
        key=lambda source: (-source.mtime_epoch, 0 if source.kind == "local" else 1),
    )


def download_remote_restore(session: RemoteSession, remote_path: str, suffix: str) -> Path:
    fd, temp_path_raw = tempfile.mkstemp(prefix="restore_remote_", suffix=suffix)
    os.close(fd)
    temp_path = Path(temp_path_raw)
    with temp_path.open("wb") as handle:
        session.download_file(remote_path, handle)
    return temp_path


def restore_source_from_path(path: Path) -> RestoreSource:
    stat = path.stat()
    return RestoreSource(
        kind="path",
        display_name=str(path),
        path=path,
        remote_path=None,
        size_bytes=stat.st_size,
        mtime_epoch=int(stat.st_mtime),
        is_temporary_local_copy=False,
    )


def current_database_size(container_name: str, service_config) -> int:
    return query_database_size(container_name, service_config)


def terminate_service_connections(
    container_name: str,
    postgres_user: str,
    database_name: str,
) -> None:
    sql = (
        "SELECT pg_terminate_backend(pid) "
        "FROM pg_stat_activity "
        f"WHERE datname = {sql_literal(database_name)} "
        "AND pid <> pg_backend_pid();"
    )
    run_psql(container_name, postgres_user, "postgres", sql)


def drop_and_recreate_service_database(
    container_name: str,
    postgres_user: str,
    database_name: str,
) -> None:
    quoted_name = sql_identifier(database_name)
    run_psql(container_name, postgres_user, "postgres", f"DROP DATABASE {quoted_name};")
    run_psql(container_name, postgres_user, "postgres", f"CREATE DATABASE {quoted_name};")


def restore_sql_file(
    container_name: str,
    postgres_user: str,
    database_name: str,
    local_sql_path: Path,
) -> None:
    with local_sql_path.open("rb") as handle:
        process = docker_exec_popen(
            container_name,
            [
                "psql",
                "-v",
                "ON_ERROR_STOP=1",
                "--username",
                postgres_user,
                "--dbname",
                database_name,
            ],
            stdin=handle,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            interactive=True,
        )
        _, stderr = process.communicate()
    if process.returncode != 0:
        raise subprocess.CalledProcessError(
            process.returncode,
            ["docker", "exec", "-i", container_name, "psql"],
            stderr=(stderr or b"").decode("utf-8", "replace"),
        )


def restore_gzip_file(
    container_name: str,
    postgres_user: str,
    database_name: str,
    local_gzip_path: Path,
) -> None:
    gunzip_process = subprocess.Popen(
        ["gzip", "-cd", str(local_gzip_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert gunzip_process.stdout is not None
    psql_process = docker_exec_popen(
        container_name,
        [
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "--username",
            postgres_user,
            "--dbname",
            database_name,
        ],
        stdin=gunzip_process.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        interactive=True,
    )
    gunzip_process.stdout.close()
    _, psql_stderr = psql_process.communicate()
    gunzip_stderr = (
        gunzip_process.stderr.read().decode("utf-8", "replace")
        if gunzip_process.stderr is not None
        else ""
    )
    gunzip_returncode = gunzip_process.wait()
    if gunzip_returncode != 0:
        raise subprocess.CalledProcessError(
            gunzip_returncode,
            ["gzip", "-cd", str(local_gzip_path)],
            stderr=gunzip_stderr,
        )
    if psql_process.returncode != 0:
        raise subprocess.CalledProcessError(
            psql_process.returncode,
            ["docker", "exec", "-i", container_name, "psql"],
            stderr=(psql_stderr or b"").decode("utf-8", "replace"),
        )
