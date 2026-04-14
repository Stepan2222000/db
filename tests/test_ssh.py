from __future__ import annotations

import io
from pathlib import Path

import pytest

from ops.core.models import GlobalConfig
from ops.core import ssh as ssh_core


def test_build_remote_config_requires_all_remote_fields() -> None:
    with pytest.raises(ValueError, match="DB_REMOTE_HOST"):
        ssh_core.build_remote_config(
            GlobalConfig(
                postgres_version="18",
                default_postgres_password=None,
            )
        )

    with pytest.raises(ValueError, match="DB_REMOTE_USER"):
        ssh_core.build_remote_config(
            GlobalConfig(
                postgres_version="18",
                default_postgres_password=None,
                remote_host="2.26.53.128",
            )
        )

    with pytest.raises(ValueError, match="DB_REMOTE_PASSWORD"):
        ssh_core.build_remote_config(
            GlobalConfig(
                postgres_version="18",
                default_postgres_password=None,
                remote_host="2.26.53.128",
                remote_user="root",
            )
        )

    with pytest.raises(ValueError, match="DB_REMOTE_BACKUP_PATH"):
        ssh_core.build_remote_config(
            GlobalConfig(
                postgres_version="18",
                default_postgres_password=None,
                remote_host="2.26.53.128",
                remote_user="root",
                remote_password="secret",
            )
        )


def test_build_remote_config_success() -> None:
    config = ssh_core.build_remote_config(
        GlobalConfig(
            postgres_version="18",
            default_postgres_password=None,
            remote_host="2.26.53.128",
            remote_port=2222,
            remote_user="root",
            remote_password="secret",
            remote_backup_path="/root/backups",
        )
    )

    assert config == ssh_core.RemoteConfig(
        host="2.26.53.128",
        port=2222,
        user="root",
        password="secret",
        backup_path="/root/backups",
    )


def test_open_remote_session_configures_paramiko_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeSFTPClient:
        pass

    class FakeSSHClient:
        def load_system_host_keys(self) -> None:
            captured["loaded_keys"] = True

        def set_missing_host_key_policy(self, policy) -> None:
            captured["policy"] = policy

        def connect(self, *args, **kwargs) -> None:
            captured["connect_args"] = args
            captured["connect_kwargs"] = kwargs

        def open_sftp(self):
            captured["opened_sftp"] = True
            return FakeSFTPClient()

    class FakeAutoAddPolicy:
        pass

    monkeypatch.setattr(ssh_core.paramiko, "SSHClient", FakeSSHClient)
    monkeypatch.setattr(ssh_core.paramiko, "AutoAddPolicy", FakeAutoAddPolicy)

    session = ssh_core.open_remote_session(
        ssh_core.RemoteConfig(
            host="2.26.53.128",
            port=2222,
            user="root",
            password="secret",
            backup_path="/root/backups",
        )
    )

    assert captured["loaded_keys"] is True
    assert isinstance(captured["policy"], FakeAutoAddPolicy)
    assert captured["connect_args"] == ("2.26.53.128",)
    assert captured["connect_kwargs"] == {
        "port": 2222,
        "username": "root",
        "password": "secret",
        "allow_agent": False,
        "look_for_keys": False,
        "timeout": ssh_core.CONNECT_TIMEOUT_SECONDS,
        "banner_timeout": ssh_core.BANNER_TIMEOUT_SECONDS,
        "auth_timeout": ssh_core.AUTH_TIMEOUT_SECONDS,
    }
    assert captured["opened_sftp"] is True
    assert isinstance(session, ssh_core.RemoteSession)


def test_remote_session_ensure_dir_creates_missing_segments() -> None:
    seen: list[tuple[str, str]] = []
    existing_paths = {"/root"}
    created_paths: list[str] = []

    class FakeSFTPClient:
        def stat(self, path: str):
            seen.append(("stat", path))
            if path not in existing_paths:
                raise FileNotFoundError(2, "missing")
            return object()

        def mkdir(self, path: str) -> None:
            seen.append(("mkdir", path))
            created_paths.append(path)
            existing_paths.add(path)

        def close(self) -> None:
            pass

    class FakeSSHClient:
        def close(self) -> None:
            pass

    session = ssh_core.RemoteSession(FakeSSHClient(), FakeSFTPClient())
    session.ensure_dir("/root/backups/host1/service1")

    assert created_paths == [
        "/root/backups",
        "/root/backups/host1",
        "/root/backups/host1/service1",
    ]
    assert seen[0] == ("stat", "/root")


def test_remote_session_ensure_dir_reraises_unexpected_stat_errors() -> None:
    class FakeSFTPClient:
        def stat(self, path: str):
            raise PermissionError(13, "denied")

        def close(self) -> None:
            pass

    class FakeSSHClient:
        def close(self) -> None:
            pass

    session = ssh_core.RemoteSession(FakeSSHClient(), FakeSFTPClient())

    with pytest.raises(PermissionError):
        session.ensure_dir("/root/backups")


def test_remote_session_upload_list_rename_remove_and_close() -> None:
    seen: list[tuple[str, object]] = []

    class FakeAttrs:
        def __init__(self, *, filename: str, size: int, mtime: int) -> None:
            self.filename = filename
            self.st_size = size
            self.st_mtime = mtime

    class FakeSFTPClient:
        def putfo(self, stream, remote_path: str, confirm: bool = False):
            seen.append(("putfo", remote_path, confirm, stream.read()))
            return FakeAttrs(filename="upload.tmp", size=19, mtime=123)

        def listdir_attr(self, path: str):
            seen.append(("listdir_attr", path))
            return [
                FakeAttrs(filename="b.sql.gz", size=2, mtime=2),
                FakeAttrs(filename="a.sql.gz", size=1, mtime=1),
            ]

        def rename(self, src: str, dst: str) -> None:
            seen.append(("rename", src, dst))

        def remove(self, path: str) -> None:
            seen.append(("remove", path))

        def close(self) -> None:
            seen.append(("sftp_close", None))

    class FakeSSHClient:
        def close(self) -> None:
            seen.append(("ssh_close", None))

    session = ssh_core.RemoteSession(FakeSSHClient(), FakeSFTPClient())

    uploaded = session.upload_stream(io.BytesIO(b"streamed-data-12345"), "/root/backups/upload.tmp")
    assert uploaded == ssh_core.RemoteEntry(
        filename="upload.tmp",
        size_bytes=19,
        mtime_epoch=123,
    )

    listed = session.list_dir("/root/backups")
    assert listed == [
        ssh_core.RemoteEntry(filename="a.sql.gz", size_bytes=1, mtime_epoch=1),
        ssh_core.RemoteEntry(filename="b.sql.gz", size_bytes=2, mtime_epoch=2),
    ]

    session.rename_file("/root/backups/upload.tmp", "/root/backups/final.sql.gz")
    session.remove_file("/root/backups/final.sql.gz")
    session.close()

    assert ("rename", "/root/backups/upload.tmp", "/root/backups/final.sql.gz") in seen
    assert ("remove", "/root/backups/final.sql.gz") in seen
    assert seen[-2:] == [("sftp_close", None), ("ssh_close", None)]


def test_remote_session_set_timeout_uses_sftp_channel() -> None:
    seen: list[tuple[str, float]] = []

    class FakeChannel:
        def settimeout(self, timeout_seconds: float) -> None:
            seen.append(("settimeout", timeout_seconds))

    class FakeSFTPClient:
        def get_channel(self) -> FakeChannel:
            return FakeChannel()

        def close(self) -> None:
            pass

    class FakeSSHClient:
        def close(self) -> None:
            pass

    session = ssh_core.RemoteSession(FakeSSHClient(), FakeSFTPClient())
    session.set_timeout(45)

    assert seen == [("settimeout", 45)]


def test_remote_session_close_still_closes_ssh_after_sftp_error() -> None:
    seen: list[str] = []

    class FakeSFTPClient:
        def close(self) -> None:
            seen.append("sftp_close")
            raise RuntimeError("sftp close failed")

    class FakeSSHClient:
        def close(self) -> None:
            seen.append("ssh_close")

    session = ssh_core.RemoteSession(FakeSSHClient(), FakeSFTPClient())

    with pytest.raises(RuntimeError, match="sftp close failed"):
        session.close()

    assert seen == ["sftp_close", "ssh_close"]
