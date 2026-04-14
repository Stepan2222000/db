from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

import pytest

from ops.core.models import GlobalConfig, ServiceConfig
from ops.operations import backup as backup_ops


def test_build_backup_runtime_config_requires_runtime_critical_fields(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="DB_BACKUP_TIMEOUT_SECONDS"):
        backup_ops.build_backup_runtime_config(
            tmp_path,
            GlobalConfig(postgres_version="18", default_postgres_password=None),
        )

    with pytest.raises(ValueError, match="DB_BACKUP_MAX_FILES must be at least 2"):
        backup_ops.build_backup_runtime_config(
            tmp_path,
            GlobalConfig(
                postgres_version="18",
                default_postgres_password=None,
                backup_timeout_seconds=60,
                backup_max_days=30,
                backup_max_files=1,
            ),
        )


def test_build_backup_runtime_config_success(tmp_path: Path) -> None:
    config = backup_ops.build_backup_runtime_config(
        tmp_path,
        GlobalConfig(
            postgres_version="18",
            default_postgres_password=None,
            backup_timeout_seconds=60,
            backup_max_days=30,
            backup_max_files=14,
        ),
    )

    assert config == backup_ops.BackupRuntimeConfig(
        timeout_seconds=60,
        max_days=30,
        max_files=14,
    )


def test_backup_candidates_filter_disabled_services(tmp_path: Path) -> None:
    services_dir = tmp_path / "services"
    services_dir.mkdir()
    (services_dir / ".env.alpha").write_text(
        "POSTGRES_USER=admin\nPOSTGRES_PASSWORD=secret\nPOSTGRES_PORT=5401\n",
        encoding="utf-8",
    )
    (services_dir / ".env.beta").write_text(
        "POSTGRES_USER=admin\nPOSTGRES_PASSWORD=secret\nPOSTGRES_PORT=5402\nPOSTGRES_BACKUP_DISABLED=1\n",
        encoding="utf-8",
    )

    assert [cfg.name for cfg in backup_ops.backup_candidates(tmp_path, None)] == ["alpha"]
    assert backup_ops.backup_candidates(tmp_path, "beta") == []


def test_backup_format_uses_service_then_global_then_default(tmp_path: Path) -> None:
    service_config = ServiceConfig(
        name="demo",
        env_path=tmp_path / "services" / ".env.demo",
        postgres_user="admin",
        postgres_password="secret",
        postgres_port=5401,
    )
    service_override = ServiceConfig(
        name="demo",
        env_path=tmp_path / "services" / ".env.demo",
        postgres_user="admin",
        postgres_password="secret",
        postgres_port=5401,
        backup_format=".sql",
    )

    assert backup_ops.resolve_dump_format(service_config, ".sql") == ".sql"
    assert backup_ops.resolve_dump_format(service_override, ".sql.gz") == ".sql"
    assert backup_ops.resolve_dump_format(service_config, None) == ".sql.gz"


def test_remote_backup_path_helpers() -> None:
    assert backup_ops.remote_backup_dir("/root/backups", "host1", "db1") == "/root/backups/host1/db1"
    assert backup_ops.remote_backup_filename(
        "db1",
        ".sql.gz",
        now=datetime(2026, 4, 14, 12, 30),
    ) == "db1_2026-04-14_12-30.sql.gz"


def test_cleanup_stale_tmp_files_only_removes_tmp() -> None:
    removed: list[str] = []

    class FakeSession:
        def list_dir(self, path: str):
            return [
                backup_ops.RemoteEntry("a.tmp", 1, 1),
                backup_ops.RemoteEntry("b.sql.gz", 1, 1),
                backup_ops.RemoteEntry("c.tmp", 1, 1),
            ]

        def remove_file(self, path: str) -> None:
            removed.append(path)

    count = backup_ops.cleanup_stale_tmp_files(FakeSession(), "/root/backups/db")

    assert count == 2
    assert removed == ["/root/backups/db/a.tmp", "/root/backups/db/c.tmp"]


def test_rotate_remote_backups_deletes_old_and_evenly_trims() -> None:
    removed: list[str] = []

    class FakeSession:
        def list_dir(self, path: str):
            return [
                backup_ops.RemoteEntry("old.sql.gz", 1, 10),
                backup_ops.RemoteEntry("keep1.sql.gz", 1, 100),
                backup_ops.RemoteEntry("keep2.sql.gz", 1, 200),
                backup_ops.RemoteEntry("keep3.sql.gz", 1, 300),
                backup_ops.RemoteEntry("keep4.sql.gz", 1, 400),
            ]

        def remove_file(self, path: str) -> None:
            removed.append(path)

    result = backup_ops.rotate_remote_backups(
        FakeSession(),
        "/root/backups/db",
        max_days=1_000_000,
        max_files=3,
    )

    assert result.deleted_old_count == 0
    assert result.deleted_trimmed_count == 2
    assert result.kept_count == 3
    assert removed == [
        "/root/backups/db/keep1.sql.gz",
        "/root/backups/db/keep3.sql.gz",
    ]


def test_rotate_remote_backups_removes_entries_older_than_cutoff(monkeypatch: pytest.MonkeyPatch) -> None:
    removed: list[str] = []

    class FakeSession:
        def list_dir(self, path: str):
            return [
                backup_ops.RemoteEntry("old.sql.gz", 1, 100),
                backup_ops.RemoteEntry("new.sql.gz", 1, 1_000_000),
            ]

        def remove_file(self, path: str) -> None:
            removed.append(path)

    monkeypatch.setattr(backup_ops.time, "time", lambda: 1_000_000)

    result = backup_ops.rotate_remote_backups(
        FakeSession(),
        "/root/backups/db",
        max_days=1,
        max_files=2,
    )

    assert result.deleted_old_count == 1
    assert result.deleted_trimmed_count == 0
    assert result.kept_count == 1
    assert removed == ["/root/backups/db/old.sql.gz"]


def test_configure_backup_file_logger_writes_fixed_backup_log(tmp_path: Path) -> None:
    logger = backup_ops.configure_backup_file_logger(tmp_path)
    logger.info("hello log")
    for handler in logger.handlers:
        handler.flush()

    assert (tmp_path / "backup.log").read_text(encoding="utf-8").strip().endswith("hello log")


def test_backup_lock_rejects_second_process(tmp_path: Path) -> None:
    with backup_ops.backup_lock(tmp_path):
        with pytest.raises(RuntimeError, match="already running"):
            with backup_ops.backup_lock(tmp_path):
                pass


def test_stream_backup_to_remote_fails_for_stopped_container(
    tmp_path: Path,
) -> None:
    service_config = ServiceConfig(
        name="demo",
        env_path=tmp_path / "services" / ".env.demo",
        postgres_user="admin",
        postgres_password="secret",
        postgres_port=5401,
        backup_format=".sql",
    )
    global_config = GlobalConfig(
        postgres_version="18",
        default_postgres_password=None,
        remote_backup_path="/root/backups",
        backup_timeout_seconds=60,
        backup_max_days=30,
        backup_max_files=14,
    )
    runtime_config = backup_ops.build_backup_runtime_config(tmp_path, global_config)

    class FakeSession:
        pass

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(backup_ops, "container_is_running", lambda name: False)
        with pytest.raises(RuntimeError, match="service container is not running"):
            backup_ops.stream_backup_to_remote(
                FakeSession(),
                "host1",
                service_config,
                global_config,
                runtime_config,
            )


def test_stream_backup_to_remote_removes_tmp_on_process_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service_config = ServiceConfig(
        name="demo",
        env_path=tmp_path / "services" / ".env.demo",
        postgres_user="admin",
        postgres_password="secret",
        postgres_port=5401,
        backup_format=".sql",
    )
    global_config = GlobalConfig(
        postgres_version="18",
        default_postgres_password=None,
        remote_backup_path="/root/backups",
        backup_timeout_seconds=60,
        backup_max_days=30,
        backup_max_files=14,
    )
    runtime_config = backup_ops.build_backup_runtime_config(tmp_path, global_config)
    removed_paths: list[str] = []

    class FakeSession:
        def ensure_dir(self, path: str) -> None:
            pass

        def list_dir(self, path: str):
            return []

        def upload_stream(self, stream, remote_path: str):
            while stream.read(8192):
                pass
            return backup_ops.RemoteEntry("demo.sql.tmp", 10, 1)

        def remove_file(self, path: str) -> None:
            removed_paths.append(path)

        def rename_file(self, src: str, dst: str) -> None:
            raise AssertionError("rename should not run")

    class FakeProcess:
        def __init__(self, returncode: int, stderr_text: str) -> None:
            self.stdout = io.BytesIO(b"payload")
            self.stderr = io.BytesIO(stderr_text.encode("utf-8"))
            self.returncode = returncode

        def wait(self, timeout=None) -> int:
            return self.returncode

        def kill(self) -> None:
            pass

    monkeypatch.setattr(backup_ops, "container_is_running", lambda name: True)

    monkeypatch.setattr(
        backup_ops,
        "stream_pg_dump_to_consumer",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("demo: pg_dump failed: boom")),
    )
    monkeypatch.setattr(
        backup_ops,
        "remote_backup_filename",
        lambda *args, **kwargs: "demo_2026-04-14_12-30.sql",
    )
    with pytest.raises(RuntimeError, match="pg_dump failed"):
        backup_ops.stream_backup_to_remote(
            FakeSession(),
            "host1",
            service_config,
            global_config,
            runtime_config,
        )
    assert removed_paths == ["/root/backups/host1/demo/demo_2026-04-14_12-30.sql.tmp"]


def test_stream_backup_to_remote_replaces_existing_final_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service_config = ServiceConfig(
        name="demo",
        env_path=tmp_path / "services" / ".env.demo",
        postgres_user="admin",
        postgres_password="secret",
        postgres_port=5401,
        backup_format=".sql",
    )
    global_config = GlobalConfig(
        postgres_version="18",
        default_postgres_password=None,
        remote_backup_path="/root/backups",
        backup_timeout_seconds=60,
        backup_max_days=30,
        backup_max_files=14,
    )
    runtime_config = backup_ops.build_backup_runtime_config(tmp_path, global_config)
    removed_paths: list[str] = []
    renamed_paths: list[tuple[str, str]] = []

    class FakeSession:
        def ensure_dir(self, path: str) -> None:
            pass

        def list_dir(self, path: str):
            return [backup_ops.RemoteEntry("demo_2026-04-14_12-30.sql", 10, 1)]

        def upload_stream(self, stream, remote_path: str):
            while stream.read(8192):
                pass
            return backup_ops.RemoteEntry("demo.sql.tmp", 10, 1)

        def remove_file(self, path: str) -> None:
            removed_paths.append(path)

        def rename_file(self, src: str, dst: str) -> None:
            renamed_paths.append((src, dst))

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = io.BytesIO(b"payload")
            self.stderr = io.BytesIO(b"")
            self.returncode = 0

        def wait(self, timeout=None) -> int:
            return self.returncode

        def kill(self) -> None:
            pass

    monkeypatch.setattr(backup_ops, "container_is_running", lambda name: True)
    monkeypatch.setattr(
        backup_ops,
        "stream_pg_dump_to_consumer",
        lambda *args, **kwargs: backup_ops.RemoteEntry("demo.sql.tmp", 10, 1),
    )
    monkeypatch.setattr(
        backup_ops,
        "remote_backup_filename",
        lambda *args, **kwargs: "demo_2026-04-14_12-30.sql",
    )

    result = backup_ops.stream_backup_to_remote(
        FakeSession(),
        "host1",
        service_config,
        global_config,
        runtime_config,
    )

    assert result.filename == "demo_2026-04-14_12-30.sql"
    assert removed_paths == ["/root/backups/host1/demo/demo_2026-04-14_12-30.sql"]
    assert renamed_paths == [
        (
            "/root/backups/host1/demo/demo_2026-04-14_12-30.sql.tmp",
            "/root/backups/host1/demo/demo_2026-04-14_12-30.sql",
        )
    ]
