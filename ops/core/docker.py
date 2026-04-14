from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import IO


@dataclass(frozen=True, slots=True)
class CommandResult:
    stdout: str
    stderr: str
    returncode: int


@dataclass(frozen=True, slots=True)
class ContainerSnapshot:
    exists: bool
    running: bool
    inspect_payload: dict[str, object] | None


def compose_up(project_root: Path, service: str | None = None) -> CommandResult:
    command = ["docker", "compose", "up", "-d"]
    if service is not None:
        command.append(service)
    return _run_capture(command, cwd=project_root)


def compose_stop(project_root: Path, service: str) -> CommandResult:
    return _run_capture(
        ["docker", "compose", "stop", service],
        cwd=project_root,
    )


def compose_down(project_root: Path) -> CommandResult:
    return _run_capture(["docker", "compose", "down"], cwd=project_root)


def docker_rm_force(
    container_name: str,
    project_root: Path | None = None,
) -> CommandResult:
    return _run_capture(
        ["docker", "rm", "-f", container_name],
        cwd=project_root,
    )


def docker_exec_capture(
    container_name: str,
    argv: list[str],
    project_root: Path | None = None,
    *,
    check: bool = True,
) -> CommandResult:
    return _run_capture(
        ["docker", "exec", container_name, *argv],
        cwd=project_root,
        check=check,
    )


def docker_exec_popen(
    container_name: str,
    argv: list[str],
    *,
    stdout: int | IO[bytes] | None,
    stderr: int | IO[bytes] | None,
    stdin: int | IO[bytes] | None = None,
    project_root: Path | None = None,
    interactive: bool = False,
) -> subprocess.Popen[bytes]:
    command = ["docker", "exec"]
    if interactive:
        command.append("-i")
    command.extend([container_name, *argv])
    return subprocess.Popen(
        command,
        cwd=project_root,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
    )


def docker_stats_no_stream(target: str | None = None) -> CommandResult:
    command = ["docker", "stats", "--no-stream", "--format", "json"]
    if target is not None:
        command.append(target)
    return _run_capture(command)


def docker_inspect_json(container_name: str) -> dict[str, object]:
    snapshot = container_snapshot(container_name)
    if not snapshot.exists:
        raise subprocess.CalledProcessError(
            1,
            ["docker", "inspect", container_name],
            output="",
            stderr="container not found",
        )
    assert snapshot.inspect_payload is not None
    return snapshot.inspect_payload


def container_snapshot(container_name: str) -> ContainerSnapshot:
    result = _run_capture(
        ["docker", "inspect", container_name],
        check=False,
    )
    if result.returncode != 0:
        return ContainerSnapshot(exists=False, running=False, inspect_payload=None)

    payload = json.loads(result.stdout)
    if not payload:
        raise ValueError(f"docker inspect returned no payload for {container_name}")
    inspect_payload = payload[0]
    return ContainerSnapshot(
        exists=True,
        running=bool(inspect_payload.get("State", {}).get("Running")),
        inspect_payload=inspect_payload,
    )


def container_exists(container_name: str) -> bool:
    return container_snapshot(container_name).exists


def container_is_running(container_name: str) -> bool:
    return container_snapshot(container_name).running


def _run_capture(
    command: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
) -> CommandResult:
    completed = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
    )
    return CommandResult(
        stdout=completed.stdout,
        stderr=completed.stderr,
        returncode=completed.returncode,
    )
