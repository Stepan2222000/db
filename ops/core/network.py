from __future__ import annotations

import socket


def detect_server_host() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("1.1.1.1", 80))
            return sock.getsockname()[0]
    except OSError as exc:
        raise RuntimeError("Could not detect server host IP") from exc
