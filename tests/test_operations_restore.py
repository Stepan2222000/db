from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest

from ops.operations import restore as restore_ops
from ops.core.ssh import RemoteEntry


def test_validate_restore_extension() -> None:
    assert restore_ops.validate_restore_extension("dump.sql") == ".sql"
    assert restore_ops.validate_restore_extension("dump.sql.gz") == ".sql.gz"
    with pytest.raises(ValueError, match="must end with .sql or .sql.gz"):
        restore_ops.validate_restore_extension("dump.zip")


def test_list_local_restore_sources_filters_service_and_suffixes(tmp_path: Path) -> None:
    dumps_dir = tmp_path / "dumps"
    dumps_dir.mkdir()
    matching = dumps_dir / "demo_2026-04-14_12-30.sql.gz"
    other_service = dumps_dir / "other_2026-04-14_12-30.sql.gz"
    invalid = dumps_dir / "demo_2026-04-14_12-30.zip"
    matching.write_bytes(b"x")
    other_service.write_bytes(b"x")
    invalid.write_bytes(b"x")

    sources = restore_ops.list_local_restore_sources(tmp_path, "demo")

    assert [source.display_name for source in sources] == [matching.name]
    assert sources[0].kind == "local"
    assert sources[0].path == matching


def test_list_remote_restore_sources_handles_missing_directory() -> None:
    class FakeSession:
        def list_dir(self, path: str):
            raise FileNotFoundError(2, "No such file")

    assert restore_ops.list_remote_restore_sources(FakeSession(), "/root/backups", "host1", "demo") == []


def test_list_remote_restore_sources_maps_remote_entries() -> None:
    class FakeSession:
        def list_dir(self, path: str):
            return [
                RemoteEntry("demo_1.sql.gz", 100, 10),
                RemoteEntry("demo_2.sql", 50, 20),
                RemoteEntry("notes.txt", 10, 5),
            ]

    sources = restore_ops.list_remote_restore_sources(FakeSession(), "/root/backups", "host1", "demo")

    assert [source.display_name for source in sources] == ["demo_1.sql.gz", "demo_2.sql"]
    assert sources[0].remote_path == "/root/backups/host1/demo/demo_1.sql.gz"
    assert sources[1].kind == "remote"


def test_build_restore_selection_sorts_newest_first_with_local_tiebreaker(tmp_path: Path) -> None:
    local = restore_ops.RestoreSource(
        kind="local",
        display_name="local.sql.gz",
        path=tmp_path / "local.sql.gz",
        remote_path=None,
        size_bytes=1,
        mtime_epoch=100,
    )
    remote_same_time = restore_ops.RestoreSource(
        kind="remote",
        display_name="remote.sql.gz",
        path=None,
        remote_path="/root/backups/remote.sql.gz",
        size_bytes=1,
        mtime_epoch=100,
    )
    newer_remote = restore_ops.RestoreSource(
        kind="remote",
        display_name="newer.sql.gz",
        path=None,
        remote_path="/root/backups/newer.sql.gz",
        size_bytes=1,
        mtime_epoch=200,
    )

    selection = restore_ops.build_restore_selection([local], [remote_same_time, newer_remote])

    assert [source.display_name for source in selection] == [
        "newer.sql.gz",
        "local.sql.gz",
        "remote.sql.gz",
    ]


def test_download_remote_restore_writes_temp_file_and_returns_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeSession:
        def download_file(self, remote_path: str, local_file) -> int:
            local_file.write(b"downloaded")
            return 10

    download_path = restore_ops.download_remote_restore(FakeSession(), "/root/backups/file.sql.gz", ".sql.gz")
    try:
        assert download_path.suffixes[-2:] == [".sql", ".gz"]
        assert download_path.read_bytes() == b"downloaded"
    finally:
        download_path.unlink(missing_ok=True)


def test_restore_source_from_path_uses_stat(tmp_path: Path) -> None:
    path = tmp_path / "dump.sql"
    path.write_text("SELECT 1;", encoding="utf-8")

    source = restore_ops.restore_source_from_path(path)

    assert source.kind == "path"
    assert source.path == path
    assert source.display_name == str(path)


def test_terminate_and_drop_create_use_postgres_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, str, str, str]] = []

    def fake_run_psql(container_name, postgres_user, database, sql, **kwargs):
        seen.append((container_name, postgres_user, database, sql))
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    monkeypatch.setattr(restore_ops, "run_psql", fake_run_psql)

    restore_ops.terminate_service_connections("demo", "admin", "demo")
    restore_ops.drop_and_recreate_service_database("demo", "admin", "demo")

    assert seen[0][2] == "postgres"
    assert "pg_terminate_backend" in seen[0][3]
    assert seen[1] == ("demo", "admin", "postgres", 'DROP DATABASE "demo";')
    assert seen[2] == ("demo", "admin", "postgres", 'CREATE DATABASE "demo";')


def test_restore_sql_file_uses_interactive_docker_exec(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    sql_path = tmp_path / "dump.sql"
    sql_path.write_text("SELECT 1;", encoding="utf-8")

    class FakeProcess:
        returncode = 0

        def communicate(self):
            return (b"", b"")

    def fake_popen(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(restore_ops, "psql_popen", fake_popen)

    restore_ops.restore_sql_file("demo", "admin", "demo", sql_path)

    assert captured["args"] == (
        "demo",
        "admin",
        "demo",
    )
    assert captured["kwargs"]["extra_argv"] == []
    assert captured["kwargs"]["interactive"] is True
    assert captured["kwargs"]["stdin"].name == str(sql_path)
    assert captured["kwargs"]["stdout"] == subprocess.PIPE
    assert captured["kwargs"]["stderr"] == subprocess.PIPE


def test_restore_gzip_file_checks_both_processes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    gzip_path = tmp_path / "dump.sql.gz"
    gzip_path.write_bytes(b"x")

    class FakeGzipProcess:
        def __init__(self) -> None:
            self.stdout = io.BytesIO(b"payload")
            self.stderr = io.BytesIO(b"")

        def wait(self, timeout=None) -> int:
            return 0

    class FakePsqlProcess:
        returncode = 0

        def communicate(self):
            return (b"", b"")

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: FakeGzipProcess())
    monkeypatch.setattr(
        restore_ops,
        "psql_popen",
        lambda *args, **kwargs: FakePsqlProcess(),
    )

    restore_ops.restore_gzip_file("demo", "admin", "demo", gzip_path)
