from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from ops.core import docker as docker_core


def test_compose_up_builds_expected_command(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, cwd=None, capture_output=False, text=False, check=False):
        captured["command"] = command
        captured["cwd"] = cwd
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["check"] = check
        return subprocess.CompletedProcess(command, 0, stdout="up", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = docker_core.compose_up(Path("/tmp/project"), "db")

    assert captured["command"] == ["docker", "compose", "up", "-d", "db"]
    assert captured["cwd"] == Path("/tmp/project")
    assert captured["capture_output"] is True
    assert captured["text"] is True
    assert captured["check"] is True
    assert result.stdout == "up"
    assert result.stderr == ""
    assert result.returncode == 0


def test_compose_stop_and_down_build_expected_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[list[str]] = []

    def fake_run(command, cwd=None, capture_output=False, text=False, check=False):
        seen.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    docker_core.compose_stop(Path("/tmp/project"), "db")
    docker_core.compose_down(Path("/tmp/project"))

    assert seen == [
        ["docker", "compose", "stop", "db"],
        ["docker", "compose", "down"],
    ]


def test_docker_exec_capture_builds_expected_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, cwd=None, capture_output=False, text=False, check=False):
        captured["command"] = command
        captured["cwd"] = cwd
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = docker_core.docker_exec_capture(
        "db-test",
        ["sh", "-c", "echo ok"],
        project_root=Path("/tmp/project"),
    )

    assert captured["command"] == [
        "docker",
        "exec",
        "db-test",
        "sh",
        "-c",
        "echo ok",
    ]
    assert captured["cwd"] == Path("/tmp/project")
    assert result.stdout == "ok\n"


def test_docker_exec_popen_builds_expected_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakePopen:
        def __init__(self, command, cwd=None, stdin=None, stdout=None, stderr=None):
            captured["command"] = command
            captured["cwd"] = cwd
            captured["stdin"] = stdin
            captured["stdout"] = stdout
            captured["stderr"] = stderr

    monkeypatch.setattr(subprocess, "Popen", FakePopen)

    popen = docker_core.docker_exec_popen(
        "db-test",
        ["pg_dump", "-U", "admin"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        project_root=Path("/tmp/project"),
    )

    assert isinstance(popen, FakePopen)
    assert captured["command"] == [
        "docker",
        "exec",
        "db-test",
        "pg_dump",
        "-U",
        "admin",
    ]
    assert captured["cwd"] == Path("/tmp/project")
    assert captured["stdout"] == subprocess.PIPE
    assert captured["stderr"] == subprocess.PIPE


def test_docker_stats_no_stream_builds_expected_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, cwd=None, capture_output=False, text=False, check=False):
        captured["command"] = command
        return subprocess.CompletedProcess(command, 0, stdout='{"Name":"db"}\n', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = docker_core.docker_stats_no_stream("db-test")

    assert captured["command"] == [
        "docker",
        "stats",
        "--no-stream",
        "--format",
        "json",
        "db-test",
    ]
    assert result.stdout == '{"Name":"db"}\n'


def test_run_capture_raises_called_process_error_when_checking() -> None:
    with pytest.raises(subprocess.CalledProcessError):
        docker_core._run_capture(
            [sys.executable, "-c", "import sys; sys.stderr.write('boom'); sys.exit(2)"],
            check=True,
        )
