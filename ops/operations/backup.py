from __future__ import annotations

import contextlib
import fcntl
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from logging import Logger
from pathlib import Path, PurePosixPath
from typing import Iterator

import logging

from ops.core.config import load_service_config
from ops.core.discovery import discover_services
from ops.core.docker import container_is_running
from ops.core.models import GlobalConfig, ServiceConfig
from ops.core.ssh import RemoteEntry, RemoteSession
from ops.operations.postgres import resolve_dump_format, stream_pg_dump_to_consumer

VALID_REMOTE_BACKUP_SUFFIXES = (".sql", ".sql.gz")


@dataclass(frozen=True, slots=True)
class BackupRuntimeConfig:
    timeout_seconds: int
    max_days: int
    max_files: int


@dataclass(frozen=True, slots=True)
class BackupResult:
    service_name: str
    remote_dir: str
    filename: str
    dump_format: str
    size_bytes: int
    tmp_cleaned_count: int


@dataclass(frozen=True, slots=True)
class RotationResult:
    deleted_old_count: int
    deleted_trimmed_count: int
    kept_count: int


def build_backup_runtime_config(
    project_root: Path,
    global_config: GlobalConfig,
) -> BackupRuntimeConfig:
    if global_config.backup_timeout_seconds is None:
        raise ValueError("DB_BACKUP_TIMEOUT_SECONDS is required for backup")
    if global_config.backup_max_days is None:
        raise ValueError("DB_BACKUP_MAX_DAYS is required for backup")
    if global_config.backup_max_files is None:
        raise ValueError("DB_BACKUP_MAX_FILES is required for backup")
    if global_config.backup_max_files < 2:
        raise ValueError("DB_BACKUP_MAX_FILES must be at least 2")

    return BackupRuntimeConfig(
        timeout_seconds=global_config.backup_timeout_seconds,
        max_days=global_config.backup_max_days,
        max_files=global_config.backup_max_files,
    )


def backup_candidates(project_root: Path, selected_name: str | None) -> list[ServiceConfig]:
    if selected_name is not None:
        service_config = load_service_config(project_root, selected_name)
        return [] if service_config.backup_disabled else [service_config]

    return [
        service_config
        for service_config in (
            load_service_config(project_root, service_name)
            for service_name in discover_services(project_root)
        )
        if not service_config.backup_disabled
    ]


def remote_backup_dir(remote_root: str, hostname: str, service_name: str) -> str:
    return str(PurePosixPath(remote_root) / hostname / service_name)


def remote_backup_filename(service_name: str, dump_format: str, now: datetime | None = None) -> str:
    timestamp = (now or datetime.now()).strftime("%Y-%m-%d_%H-%M")
    return f"{service_name}_{timestamp}{dump_format}"


def cleanup_stale_tmp_files(session: RemoteSession, remote_dir: str) -> int:
    removed = 0
    for entry in session.list_dir(remote_dir):
        if entry.filename.endswith(".tmp"):
            session.remove_file(f"{remote_dir}/{entry.filename}")
            removed += 1
    return removed


def rotate_remote_backups(
    session: RemoteSession,
    remote_dir: str,
    max_days: int,
    max_files: int,
) -> RotationResult:
    cutoff_epoch = int(time.time()) - (max_days * 24 * 60 * 60)
    entries = [
        entry
        for entry in session.list_dir(remote_dir)
        if entry.filename.endswith(VALID_REMOTE_BACKUP_SUFFIXES)
    ]

    deleted_old_count = 0
    remaining_entries: list[RemoteEntry] = []
    for entry in entries:
        if entry.mtime_epoch < cutoff_epoch:
            session.remove_file(f"{remote_dir}/{entry.filename}")
            deleted_old_count += 1
        else:
            remaining_entries.append(entry)

    remaining_entries.sort(key=lambda item: item.mtime_epoch)
    if len(remaining_entries) <= max_files:
        return RotationResult(
            deleted_old_count=deleted_old_count,
            deleted_trimmed_count=0,
            kept_count=len(remaining_entries),
        )

    kept_indexes = {
        round(index * (len(remaining_entries) - 1) / (max_files - 1))
        for index in range(max_files)
    }
    deleted_trimmed_count = 0
    for index, entry in enumerate(remaining_entries):
        if index not in kept_indexes:
            session.remove_file(f"{remote_dir}/{entry.filename}")
            deleted_trimmed_count += 1

    return RotationResult(
        deleted_old_count=deleted_old_count,
        deleted_trimmed_count=deleted_trimmed_count,
        kept_count=len(kept_indexes),
    )


def configure_backup_file_logger(project_root: Path) -> Logger:
    logger = logging.getLogger(f"ops.backup.file.{project_root}")
    if logger.handlers:
        return logger

    handler = logging.FileHandler(project_root / "backup.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger


@contextmanager
def backup_lock(project_root: Path) -> Iterator[None]:
    lock_handle = (project_root / "backup.lock").open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Another backup process is already running") from exc
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        lock_handle.close()


def stream_backup_to_remote(
    session: RemoteSession,
    hostname: str,
    service_config: ServiceConfig,
    global_config: GlobalConfig,
    runtime_config: BackupRuntimeConfig,
) -> BackupResult:
    container_name = service_config.name
    if not container_is_running(container_name):
        raise RuntimeError(f"{container_name}: service container is not running")

    dump_format = resolve_dump_format(service_config, global_config.backup_format)
    remote_dir = remote_backup_dir(global_config.remote_backup_path, hostname, container_name)
    final_name = remote_backup_filename(container_name, dump_format)
    tmp_name = f"{final_name}.tmp"
    remote_tmp_path = f"{remote_dir}/{tmp_name}"
    remote_final_path = f"{remote_dir}/{final_name}"

    session.ensure_dir(remote_dir)
    existing_entries = session.list_dir(remote_dir)
    tmp_cleaned_count = _cleanup_tmp_entries(session, remote_dir, existing_entries)
    if dump_format == ".sql":
        size_bytes = _stream_plain_dump(session, container_name, service_config, remote_tmp_path, runtime_config)
    else:
        size_bytes = _stream_gzip_dump(session, container_name, service_config, remote_tmp_path, runtime_config)
    if any(entry.filename == final_name for entry in existing_entries):
        session.remove_file(f"{remote_dir}/{final_name}")
    session.rename_file(remote_tmp_path, remote_final_path)

    return BackupResult(
        service_name=container_name,
        remote_dir=remote_dir,
        filename=final_name,
        dump_format=dump_format,
        size_bytes=size_bytes,
        tmp_cleaned_count=tmp_cleaned_count,
    )


def _stream_plain_dump(
    session: RemoteSession,
    container_name: str,
    service_config: ServiceConfig,
    remote_tmp_path: str,
    runtime_config: BackupRuntimeConfig,
) -> int:
    try:
        uploaded = stream_pg_dump_to_consumer(
            container_name,
            service_config,
            ".sql",
            lambda stream: session.upload_stream(stream, remote_tmp_path),
            timeout_seconds=runtime_config.timeout_seconds,
        )
    except Exception:
        _remove_remote_tmp(session, remote_tmp_path)
        raise
    return uploaded.size_bytes


def _stream_gzip_dump(
    session: RemoteSession,
    container_name: str,
    service_config: ServiceConfig,
    remote_tmp_path: str,
    runtime_config: BackupRuntimeConfig,
) -> int:
    try:
        uploaded = stream_pg_dump_to_consumer(
            container_name,
            service_config,
            ".sql.gz",
            lambda stream: session.upload_stream(stream, remote_tmp_path),
            timeout_seconds=runtime_config.timeout_seconds,
        )
    except Exception:
        _remove_remote_tmp(session, remote_tmp_path)
        raise
    return uploaded.size_bytes


def _remove_remote_tmp(session: RemoteSession, remote_tmp_path: str) -> None:
    with contextlib.suppress(OSError):
        session.remove_file(remote_tmp_path)


def _cleanup_tmp_entries(
    session: RemoteSession,
    remote_dir: str,
    entries: list[RemoteEntry],
) -> int:
    removed = 0
    for entry in entries:
        if entry.filename.endswith(".tmp"):
            session.remove_file(f"{remote_dir}/{entry.filename}")
            removed += 1
    return removed
