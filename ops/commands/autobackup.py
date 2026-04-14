from __future__ import annotations

import logging
from pathlib import Path

import typer

from ops.commands.backup import backup as backup_command
from ops.operations.autobackup import (
    AutobackupStatus,
    build_autobackup_status,
    install_autobackup,
    read_backup_log_tail,
    uninstall_autobackup,
)

LOGGER = logging.getLogger(__name__)
app = typer.Typer(no_args_is_help=True)


@app.command()
def install() -> None:
    project_root = Path.cwd()
    backup_installed, metrics_installed = install_autobackup(project_root)
    LOGGER.info("Backup cron installed: %s", "yes" if backup_installed else "no")
    LOGGER.info("Metrics cron installed: %s", "yes" if metrics_installed else "no")


@app.command()
def uninstall() -> None:
    project_root = Path.cwd()
    removed_backup, removed_metrics = uninstall_autobackup(project_root)
    LOGGER.info("Removed backup cron jobs: %d", removed_backup)
    LOGGER.info("Removed metrics cron jobs: %d", removed_metrics)


@app.command()
def status() -> None:
    status_result = build_autobackup_status(Path.cwd())
    LOGGER.info("Backup enabled: %s", "yes" if status_result.backup_enabled else "no")
    LOGGER.info("Backup schedule: %s", status_result.backup_schedule or "unset")
    LOGGER.info("Metrics enabled: %s", "yes" if status_result.metrics_enabled else "no")
    LOGGER.info(
        "Metrics interval: %s",
        status_result.metrics_interval_minutes
        if status_result.metrics_interval_minutes is not None
        else "unset",
    )
    LOGGER.info("Backup cron installed: %s", "yes" if status_result.backup_job_installed else "no")
    LOGGER.info("Metrics cron installed: %s", "yes" if status_result.metrics_job_installed else "no")
    for service_name, backup_enabled in status_result.service_backup_flags:
        LOGGER.info(
            "service %s: backup %s",
            service_name,
            "enabled" if backup_enabled else "disabled",
        )


@app.command()
def test() -> None:
    backup_command(None)


@app.command()
def logs() -> None:
    tail = read_backup_log_tail(Path.cwd())
    if not tail:
        LOGGER.info("No backup log found")
        return
    for line in tail.splitlines():
        LOGGER.info(line)
