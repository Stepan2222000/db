from __future__ import annotations

import logging
import socket
from pathlib import Path

import typer

from ops.core.config import load_global_config
from ops.core.ssh import build_remote_config, open_remote_session
from ops.operations.backup import (
    BackupResult,
    backup_candidates,
    backup_lock,
    build_backup_runtime_config,
    configure_backup_file_logger,
    rotate_remote_backups,
    stream_backup_to_remote,
)

LOGGER = logging.getLogger(__name__)


def backup(name: str | None = typer.Argument(None, metavar="NAME")) -> None:
    project_root = Path.cwd()
    file_logger = configure_backup_file_logger(project_root)

    try:
        global_config = load_global_config(project_root)
        runtime_config = build_backup_runtime_config(project_root, global_config)
        remote_config = build_remote_config(global_config)
        candidates = backup_candidates(project_root, name)
        if not candidates:
            LOGGER.info("No services selected for backup")
            file_logger.info("No services selected for backup")
            return

        with backup_lock(project_root):
            with open_remote_session(remote_config) as session:
                session.set_timeout(runtime_config.timeout_seconds)
                hostname = socket.gethostname()
                results = []
                rotation_warnings = 0

                for service_config in candidates:
                    result = stream_backup_to_remote(
                        session,
                        hostname,
                        service_config,
                        global_config,
                        runtime_config,
                    )
                    results.append(result)
                    LOGGER.info("%s: uploaded %s", result.service_name, result.filename)
                    file_logger.info(
                        "%s: uploaded %s (%d bytes)",
                        result.service_name,
                        result.filename,
                        result.size_bytes,
                    )
                    try:
                        rotation = rotate_remote_backups(
                            session,
                            result.remote_dir,
                            runtime_config.max_days,
                            runtime_config.max_files,
                        )
                        file_logger.info(
                            "%s: rotation old=%d trimmed=%d kept=%d",
                            result.service_name,
                            rotation.deleted_old_count,
                            rotation.deleted_trimmed_count,
                            rotation.kept_count,
                        )
                    except Exception:
                        rotation_warnings += 1
                        LOGGER.warning("%s: rotation failed", result.service_name)
                        file_logger.exception("%s: rotation failed", result.service_name)

        tmp_cleaned_count = sum(result.tmp_cleaned_count for result in results)
        LOGGER.info(
            "Backup finished: selected=%d uploaded=%d tmp_cleaned=%d rotation_warnings=%d",
            len(candidates),
            len(results),
            tmp_cleaned_count,
            rotation_warnings,
        )
        file_logger.info(
            "Backup finished: selected=%d uploaded=%d tmp_cleaned=%d rotation_warnings=%d",
            len(candidates),
            len(results),
            tmp_cleaned_count,
            rotation_warnings,
        )
    except Exception:
        file_logger.exception("Backup failed")
        raise
