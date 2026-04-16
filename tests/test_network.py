from __future__ import annotations

import socket

import pytest

from ops.core import network as network_core


def test_detect_server_host_uses_route_source_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def connect(self, target) -> None:
            self.target = target

        def getsockname(self):
            return ("2.26.53.128", 12345)

    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: FakeSocket())

    assert network_core.detect_server_host() == "2.26.53.128"


def test_detect_server_host_fails_when_route_source_ip_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingSocket:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def connect(self, target) -> None:
            raise OSError("route unavailable")

    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: FailingSocket())

    with pytest.raises(RuntimeError, match="Could not detect server host IP"):
        network_core.detect_server_host()
