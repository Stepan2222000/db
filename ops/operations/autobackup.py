from __future__ import annotations

import json
from collections import deque
import shlex
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from crontab import CronItem, CronSlices, CronTab

from ops.core.config import load_global_config
from ops.core.docker import docker_stats_no_stream
from ops.operations.services import load_all_service_configs

BACKUP_CRON_COMMENT = "db-backup"
METRICS_CRON_COMMENT = "db-metrics"


@dataclass(frozen=True, slots=True)
class AutobackupStatus:
    backup_enabled: bool
    backup_schedule: str | None
    metrics_enabled: bool
    metrics_interval_minutes: int | None
    backup_job_installed: bool
    metrics_job_installed: bool
    service_backup_flags: list[tuple[str, bool]]


@dataclass(frozen=True, slots=True)
class MetricsSample:
    ts_epoch: int
    container_name: str
    cpu_percent: str
    mem_usage: str
    mem_percent: str
    net_io: str
    block_io: str
    pids: str
def build_backup_cron_command(project_root: Path) -> str:
    log_path = project_root / "backup.log"
    command = _build_project_python_command(project_root, "ops/cli.py backup")
    return (
        f"{command} >> {shlex.quote(str(log_path))} 2>&1"
    )


def build_metrics_cron_command(project_root: Path) -> str:
    code = (
        "from pathlib import Path; "
        "from ops.operations.autobackup import run_metrics_now; "
        "run_metrics_now(Path.cwd())"
    )
    return _build_project_python_command(project_root, f"-c {shlex.quote(code)}")


def load_user_crontab() -> CronTab:
    return CronTab(user=True)


def find_job(cron: CronTab, comment: str) -> CronItem | None:
    return next(iter(cron.find_comment(comment)), None)


def _build_project_python_command(project_root: Path, command: str) -> str:
    python_path = project_root / ".venv" / "bin" / "python"
    return f"cd {shlex.quote(str(project_root))} && {shlex.quote(str(python_path))} {command}"


def install_autobackup(project_root: Path) -> tuple[bool, bool]:
    global_config = load_global_config(project_root)
    cron = load_user_crontab()

    backup_installed = False
    if global_config.backup_enabled:
        if not global_config.backup_schedule:
            raise ValueError("DB_BACKUP_SCHEDULE is required when backup is enabled")
        if not CronSlices.is_valid(global_config.backup_schedule):
            raise ValueError("DB_BACKUP_SCHEDULE is not a valid cron expression")
        cron.remove_all(comment=BACKUP_CRON_COMMENT)
        job = cron.new(command=build_backup_cron_command(project_root), comment=BACKUP_CRON_COMMENT)
        job.setall(global_config.backup_schedule)
        backup_installed = True
    else:
        cron.remove_all(comment=BACKUP_CRON_COMMENT)

    metrics_installed = False
    if global_config.metrics_enabled:
        if global_config.metrics_interval_minutes is None:
            raise ValueError("DB_METRICS_INTERVAL_MINUTES is required when metrics are enabled")
        if not 1 <= global_config.metrics_interval_minutes <= 59:
            raise ValueError("DB_METRICS_INTERVAL_MINUTES must be within 1..59")
        cron.remove_all(comment=METRICS_CRON_COMMENT)
        job = cron.new(command=build_metrics_cron_command(project_root), comment=METRICS_CRON_COMMENT)
        job.setall(f"*/{global_config.metrics_interval_minutes} * * * *")
        metrics_installed = True
    else:
        cron.remove_all(comment=METRICS_CRON_COMMENT)

    cron.write()
    return backup_installed, metrics_installed


def uninstall_autobackup(project_root: Path) -> tuple[int, int]:
    cron = load_user_crontab()
    removed_backup = cron.remove_all(comment=BACKUP_CRON_COMMENT)
    removed_metrics = cron.remove_all(comment=METRICS_CRON_COMMENT)
    cron.write()
    return removed_backup, removed_metrics


def build_autobackup_status(project_root: Path) -> AutobackupStatus:
    global_config = load_global_config(project_root)
    cron = load_user_crontab()
    service_flags = [
        (service_config.name, not service_config.backup_disabled)
        for service_config in load_all_service_configs(project_root)
    ]
    return AutobackupStatus(
        backup_enabled=global_config.backup_enabled,
        backup_schedule=global_config.backup_schedule,
        metrics_enabled=global_config.metrics_enabled,
        metrics_interval_minutes=global_config.metrics_interval_minutes,
        backup_job_installed=find_job(cron, BACKUP_CRON_COMMENT) is not None,
        metrics_job_installed=find_job(cron, METRICS_CRON_COMMENT) is not None,
        service_backup_flags=service_flags,
    )


def collect_metrics_samples(project_root: Path) -> list[MetricsSample]:
    samples: list[MetricsSample] = []
    timestamp = int(time.time())
    service_names = {service_config.name for service_config in load_all_service_configs(project_root)}
    for raw_line in docker_stats_no_stream().stdout.splitlines():
        if not raw_line.strip():
            continue
        payload = json.loads(raw_line)
        if payload["Name"] not in service_names:
            continue
        samples.append(
            MetricsSample(
                ts_epoch=timestamp,
                container_name=payload["Name"],
                cpu_percent=payload["CPUPerc"],
                mem_usage=payload["MemUsage"],
                mem_percent=payload["MemPerc"],
                net_io=payload["NetIO"],
                block_io=payload["BlockIO"],
                pids=payload["PIDs"],
            )
        )
    return samples


def ensure_metrics_db(project_root: Path) -> Path:
    return project_root / "metrics.db"


def write_metrics_samples(project_root: Path, samples: list[MetricsSample]) -> int:
    if not samples:
        return 0

    metrics_db = ensure_metrics_db(project_root)
    connection = sqlite3.connect(metrics_db)
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS metrics (
                ts INTEGER,
                container_name TEXT,
                cpu_percent TEXT,
                mem_usage TEXT,
                mem_percent TEXT,
                net_io TEXT,
                block_io TEXT,
                pids TEXT
            )
            """
        )
        connection.executemany(
            """
            INSERT INTO metrics (
                ts, container_name, cpu_percent, mem_usage,
                mem_percent, net_io, block_io, pids
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    sample.ts_epoch,
                    sample.container_name,
                    sample.cpu_percent,
                    sample.mem_usage,
                    sample.mem_percent,
                    sample.net_io,
                    sample.block_io,
                    sample.pids,
                )
                for sample in samples
            ],
        )
        connection.commit()
    finally:
        connection.close()
    return len(samples)


def run_metrics_now(project_root: Path) -> int:
    return write_metrics_samples(project_root, collect_metrics_samples(project_root))


def read_backup_log_tail(project_root: Path, lines: int = 50) -> str:
    if lines <= 0:
        return ""
    log_path = project_root / "backup.log"
    if not log_path.exists():
        return ""
    with log_path.open("r", encoding="utf-8") as handle:
        return "\n".join(deque((line.rstrip("\n") for line in handle), maxlen=lines))
