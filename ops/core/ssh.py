from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import BinaryIO

import paramiko

from ops.core.models import GlobalConfig

CONNECT_TIMEOUT_SECONDS = 10
BANNER_TIMEOUT_SECONDS = 10
AUTH_TIMEOUT_SECONDS = 10


@dataclass(frozen=True, slots=True)
class RemoteConfig:
    host: str
    port: int
    user: str
    password: str
    backup_path: str


@dataclass(frozen=True, slots=True)
class RemoteEntry:
    filename: str
    size_bytes: int
    mtime_epoch: int


@dataclass(slots=True)
class RemoteSession:
    _ssh_client: paramiko.SSHClient
    _sftp_client: paramiko.SFTPClient

    def __enter__(self) -> RemoteSession:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def ensure_dir(self, path: str) -> None:
        if not path or path == ".":
            return

        current_path = PurePosixPath("/") if path.startswith("/") else PurePosixPath()
        for part in PurePosixPath(path).parts:
            if part == "/":
                continue
            current_path /= part
            try:
                self._sftp_client.stat(str(current_path))
            except OSError as exc:
                if getattr(exc, "errno", None) != 2:
                    raise
                self._sftp_client.mkdir(str(current_path))

    def upload_stream(self, stream: BinaryIO, remote_path: str) -> RemoteEntry:
        attrs = self._sftp_client.putfo(stream, remote_path, confirm=True)
        return RemoteEntry(
            filename=PurePosixPath(remote_path).name,
            size_bytes=int(attrs.st_size),
            mtime_epoch=int(getattr(attrs, "st_mtime", 0) or 0),
        )

    def list_dir(self, path: str) -> list[RemoteEntry]:
        return sorted(
            [
                RemoteEntry(
                    filename=item.filename,
                    size_bytes=int(item.st_size),
                    mtime_epoch=int(getattr(item, "st_mtime", 0) or 0),
                )
                for item in self._sftp_client.listdir_attr(path)
            ],
            key=lambda item: item.filename,
        )

    def remove_file(self, path: str) -> None:
        self._sftp_client.remove(path)

    def rename_file(self, src: str, dst: str) -> None:
        self._sftp_client.rename(src, dst)

    def close(self) -> None:
        try:
            self._sftp_client.close()
        finally:
            self._ssh_client.close()


def build_remote_config(global_config: GlobalConfig) -> RemoteConfig:
    missing_field = next(
        (
            field_name
            for field_name, value in (
                ("DB_REMOTE_HOST", global_config.remote_host),
                ("DB_REMOTE_USER", global_config.remote_user),
                ("DB_REMOTE_PASSWORD", global_config.remote_password),
                ("DB_REMOTE_BACKUP_PATH", global_config.remote_backup_path),
            )
            if not value
        ),
        None,
    )
    if missing_field:
        raise ValueError(f"{missing_field} is required for remote operations")

    return RemoteConfig(
        host=global_config.remote_host,
        port=global_config.remote_port,
        user=global_config.remote_user,
        password=global_config.remote_password,
        backup_path=global_config.remote_backup_path,
    )


def open_remote_session(config: RemoteConfig) -> RemoteSession:
    ssh_client = paramiko.SSHClient()
    ssh_client.load_system_host_keys()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh_client.connect(
        config.host,
        port=config.port,
        username=config.user,
        password=config.password,
        allow_agent=False,
        look_for_keys=False,
        timeout=CONNECT_TIMEOUT_SECONDS,
        banner_timeout=BANNER_TIMEOUT_SECONDS,
        auth_timeout=AUTH_TIMEOUT_SECONDS,
    )
    return RemoteSession(ssh_client, ssh_client.open_sftp())
