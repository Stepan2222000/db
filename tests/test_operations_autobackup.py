from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from crontab import CronTab

from ops.operations import autobackup as autobackup_ops


def test_is_enabled_flag_truthy_and_falsy() -> None:
    assert autobackup_ops.is_enabled_flag("1") is True
    assert autobackup_ops.is_enabled_flag("yes") is True
    assert autobackup_ops.is_enabled_flag("0") is False
    assert autobackup_ops.is_enabled_flag("false") is False
    assert autobackup_ops.is_enabled_flag(None) is False


def test_install_autobackup_writes_backup_and_metrics_jobs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tabfile = tmp_path / "crontab"
    tabfile.write_text("", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "DB_BACKUP_ENABLED=1\n"
        "DB_BACKUP_SCHEDULE=*/15 * * * *\n"
        "DB_METRICS_ENABLED=1\n"
        "DB_METRICS_INTERVAL_MINUTES=5\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        autobackup_ops,
        "load_user_crontab",
        lambda: CronTab(tabfile=str(tabfile)),
    )

    backup_installed, metrics_installed = autobackup_ops.install_autobackup(tmp_path)
    cron = CronTab(tabfile=str(tabfile))

    assert backup_installed is True
    assert metrics_installed is True
    jobs = [(job.comment, job.slices.render(), job.command) for job in cron]
    assert jobs[0][0] == autobackup_ops.BACKUP_CRON_COMMENT
    assert jobs[0][1] == "*/15 * * * *"
    assert jobs[1][0] == autobackup_ops.METRICS_CRON_COMMENT
    assert jobs[1][1] == "*/5 * * * *"


def test_install_autobackup_rejects_invalid_schedule(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tabfile = tmp_path / "crontab"
    tabfile.write_text("", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "DB_BACKUP_ENABLED=1\n"
        "DB_BACKUP_SCHEDULE=61 * * * *\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        autobackup_ops,
        "load_user_crontab",
        lambda: CronTab(tabfile=str(tabfile)),
    )

    with pytest.raises(ValueError, match="DB_BACKUP_SCHEDULE is not a valid cron expression"):
        autobackup_ops.install_autobackup(tmp_path)


def test_install_autobackup_requires_valid_metrics_interval(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tabfile = tmp_path / "crontab"
    tabfile.write_text("", encoding="utf-8")
    (tmp_path / ".env").write_text(
        "DB_METRICS_ENABLED=1\n"
        "DB_METRICS_INTERVAL_MINUTES=60\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        autobackup_ops,
        "load_user_crontab",
        lambda: CronTab(tabfile=str(tabfile)),
    )

    with pytest.raises(ValueError, match="DB_METRICS_INTERVAL_MINUTES must be within 1..59"):
        autobackup_ops.install_autobackup(tmp_path)


def test_uninstall_autobackup_removes_jobs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tabfile = tmp_path / "crontab"
    tabfile.write_text("", encoding="utf-8")
    cron = CronTab(tabfile=str(tabfile))
    cron.new(command="echo backup", comment=autobackup_ops.BACKUP_CRON_COMMENT).setall("*/5 * * * *")
    cron.new(command="echo metrics", comment=autobackup_ops.METRICS_CRON_COMMENT).setall("*/1 * * * *")
    cron.write()

    monkeypatch.setattr(
        autobackup_ops,
        "load_user_crontab",
        lambda: CronTab(tabfile=str(tabfile)),
    )

    removed_backup, removed_metrics = autobackup_ops.uninstall_autobackup(tmp_path)
    remaining = list(CronTab(tabfile=str(tabfile)))

    assert removed_backup == 1
    assert removed_metrics == 1
    assert remaining == []


def test_build_autobackup_status_reports_flags_and_jobs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tabfile = tmp_path / "crontab"
    tabfile.write_text("", encoding="utf-8")
    cron = CronTab(tabfile=str(tabfile))
    cron.new(command="echo backup", comment=autobackup_ops.BACKUP_CRON_COMMENT).setall("*/5 * * * *")
    cron.write()
    services_dir = tmp_path / "services"
    services_dir.mkdir()
    (tmp_path / ".env").write_text(
        "DB_BACKUP_ENABLED=1\n"
        "DB_BACKUP_SCHEDULE=*/5 * * * *\n"
        "DB_METRICS_ENABLED=0\n",
        encoding="utf-8",
    )
    (services_dir / ".env.alpha").write_text(
        "POSTGRES_USER=admin\nPOSTGRES_PASSWORD=secret\nPOSTGRES_PORT=5401\n",
        encoding="utf-8",
    )
    (services_dir / ".env.beta").write_text(
        "POSTGRES_USER=admin\nPOSTGRES_PASSWORD=secret\nPOSTGRES_PORT=5402\nPOSTGRES_BACKUP_DISABLED=1\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        autobackup_ops,
        "load_user_crontab",
        lambda: CronTab(tabfile=str(tabfile)),
    )

    status = autobackup_ops.build_autobackup_status(tmp_path)

    assert status.backup_enabled is True
    assert status.metrics_enabled is False
    assert status.backup_job_installed is True
    assert status.metrics_job_installed is False
    assert status.service_backup_flags == [("alpha", True), ("beta", False)]


def test_collect_metrics_samples_filters_stopped_containers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    services_dir = tmp_path / "services"
    services_dir.mkdir()
    (services_dir / ".env.alpha").write_text(
        "POSTGRES_USER=admin\nPOSTGRES_PASSWORD=secret\nPOSTGRES_PORT=5401\n",
        encoding="utf-8",
    )
    (services_dir / ".env.beta").write_text(
        "POSTGRES_USER=admin\nPOSTGRES_PASSWORD=secret\nPOSTGRES_PORT=5402\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(autobackup_ops, "container_exists", lambda name: True)
    monkeypatch.setattr(autobackup_ops, "container_is_running", lambda name: name == "alpha")
    monkeypatch.setattr(
        autobackup_ops,
        "docker_stats_no_stream",
        lambda name: type(
            "StatsResult",
            (),
            {
                "stdout": json.dumps(
                    {
                        "Name": name,
                        "CPUPerc": "1.00%",
                        "MemUsage": "1MiB / 1GiB",
                        "MemPerc": "0.10%",
                        "NetIO": "1kB / 2kB",
                        "BlockIO": "3kB / 4kB",
                        "PIDs": "5",
                    }
                )
            },
        )(),
    )

    samples = autobackup_ops.collect_metrics_samples(tmp_path)

    assert len(samples) == 1
    assert samples[0].container_name == "alpha"


def test_write_metrics_samples_creates_sqlite_table_and_inserts(tmp_path: Path) -> None:
    count = autobackup_ops.write_metrics_samples(
        tmp_path,
        [
            autobackup_ops.MetricsSample(
                ts_epoch=1,
                container_name="demo",
                cpu_percent="1.00%",
                mem_usage="1MiB / 1GiB",
                mem_percent="0.10%",
                net_io="1kB / 2kB",
                block_io="3kB / 4kB",
                pids="5",
            )
        ],
    )

    assert count == 1
    con = sqlite3.connect(tmp_path / "metrics.db")
    try:
        row = con.execute("SELECT container_name, cpu_percent FROM metrics").fetchone()
    finally:
        con.close()
    assert row == ("demo", "1.00%")


def test_read_backup_log_tail_returns_last_lines(tmp_path: Path) -> None:
    (tmp_path / "backup.log").write_text(
        "\n".join(f"line {i}" for i in range(1, 11)) + "\n",
        encoding="utf-8",
    )

    assert autobackup_ops.read_backup_log_tail(tmp_path, lines=3) == "line 8\nline 9\nline 10"
