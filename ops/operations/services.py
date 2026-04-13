from __future__ import annotations

from difflib import unified_diff
from pathlib import Path
from typing import Iterable

from ops.core.config import load_service_config, service_env_path
from ops.core.discovery import discover_services
from ops.core.docker import container_exists, container_is_running, docker_inspect_json
from ops.core.env_files import write_env_file
from ops.core.models import GlobalConfig, ServiceConfig


def pick_service_port(
    existing_service_configs: Iterable[ServiceConfig],
    requested_port: int | None = None,
) -> int:
    occupied_ports = {config.postgres_port for config in existing_service_configs}

    if requested_port is not None:
        if not 1024 <= requested_port <= 65535:
            raise ValueError("Requested port must be within 1024..65535")
        if requested_port in occupied_ports:
            raise ValueError(f"Requested port {requested_port} is already in use")
        return requested_port

    candidate = 5401
    while candidate in occupied_ports:
        candidate += 1
    return candidate


def create_service_env(
    project_root: Path,
    service_name: str,
    password: str,
    port: int,
) -> Path:
    env_path = service_env_path(project_root, service_name)
    env_path.parent.mkdir(parents=True, exist_ok=True)
    write_env_file(
        env_path,
        {
            "POSTGRES_USER": "admin",
            "POSTGRES_PASSWORD": password,
            "POSTGRES_PORT": str(port),
        },
    )
    return env_path


def ensure_service_data_dir(project_root: Path, service_name: str) -> tuple[Path, bool]:
    data_dir = project_root / "data" / service_name
    existed = data_dir.exists()
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir, not existed


def resolve_add_password(
    global_config: GlobalConfig,
    requested_password: str | None,
) -> str:
    if requested_password:
        return requested_password
    if global_config.default_postgres_password:
        return global_config.default_postgres_password
    raise ValueError(
        "Password is required: pass --password or set DB_DEFAULT_POSTGRES_PASSWORD in .env"
    )


def load_all_service_configs(project_root: Path) -> list[ServiceConfig]:
    return [
        load_service_config(project_root, service_name)
        for service_name in discover_services(project_root)
    ]


def regenerate_compose_with_previous_state(project_root: Path) -> tuple[Path, str | None]:
    from ops.cli import generate_compose

    compose_path = project_root / "compose.yaml"
    previous_text = None
    if compose_path.exists():
        previous_text = compose_path.read_text(encoding="utf-8")
    new_path = generate_compose(project_root)
    return new_path, previous_text


def compose_diff_lines(project_root: Path, previous_compose_text: str | None) -> list[str]:
    compose_text = (project_root / "compose.yaml").read_text(encoding="utf-8")
    previous_lines = previous_compose_text.splitlines(keepends=True) if previous_compose_text else []
    current_lines = compose_text.splitlines(keepends=True)
    return list(
        unified_diff(
            previous_lines,
            current_lines,
            fromfile="compose.yaml (old)",
            tofile="compose.yaml (new)",
            lineterm="",
        )
    )


def summarize_apply_changes(
    project_root: Path,
    desired_service_configs: list[ServiceConfig],
    previous_compose_text: str | None,
    created_data_dirs: set[str] | None = None,
) -> list[str]:
    lines: list[str] = []
    compose_path = project_root / "compose.yaml"
    current_compose_text = compose_path.read_text(encoding="utf-8")
    created_data_dirs = created_data_dirs or set()

    if previous_compose_text is None:
        lines.append("compose.yaml: created")
    elif previous_compose_text == current_compose_text:
        lines.append("compose.yaml: unchanged")
    else:
        lines.append("compose.yaml: changed")

    for service_config in sorted(desired_service_configs, key=lambda item: item.name):
        lines.extend(_summarize_service(service_config, created_data_dirs))

    return lines


def _summarize_service(
    service_config: ServiceConfig,
    created_data_dirs: set[str],
) -> list[str]:
    container_name = service_config.name
    if not container_exists(container_name):
        summary = [f"service {service_config.name}: will be created"]
        if service_config.name in created_data_dirs:
            summary.append("data dir: created")
        return summary

    inspect_payload = docker_inspect_json(container_name)
    running = container_is_running(container_name)
    summary = [
        f"service {service_config.name}: {'running' if running else 'stopped'}"
    ]

    env_map = _env_map(inspect_payload)
    current_user = env_map.get("POSTGRES_USER")
    current_password = env_map.get("POSTGRES_PASSWORD")
    current_memory_limit = env_map.get("POSTGRES_MEMORY_LIMIT")
    current_cpu_limit = env_map.get("POSTGRES_CPU_LIMIT")

    if current_user != service_config.postgres_user:
        summary.append(f"user: {current_user} -> {service_config.postgres_user}")
    if current_password != service_config.postgres_password:
        summary.append("password: changed")

    current_port = _current_host_port(inspect_payload)
    if current_port != str(service_config.postgres_port):
        summary.append(f"port: {current_port or 'none'} -> {service_config.postgres_port}")

    current_max_connections = _current_max_connections(inspect_payload)
    desired_max_connections = (
        str(service_config.max_connections)
        if service_config.max_connections is not None
        else None
    )
    if current_max_connections != desired_max_connections:
        summary.append(
            f"max_connections: {current_max_connections or 'default'} -> "
            f"{desired_max_connections or 'default'}"
        )

    if (
        current_memory_limit != service_config.memory_limit
        or current_cpu_limit != service_config.cpu_limit
    ):
        summary.append("resources: changed")
    if service_config.name in created_data_dirs:
        summary.append("data dir: created")

    return summary


def _env_map(inspect_payload: dict[str, object]) -> dict[str, str]:
    env_lines = inspect_payload.get("Config", {}).get("Env", [])
    env_map: dict[str, str] = {}
    for env_line in env_lines:
        if "=" not in env_line:
            continue
        key, value = env_line.split("=", 1)
        env_map[key] = value
    return env_map


def _current_host_port(inspect_payload: dict[str, object]) -> str | None:
    port_bindings = inspect_payload.get("HostConfig", {}).get("PortBindings", {})
    bindings = port_bindings.get("5432/tcp") or []
    if not bindings:
        return None
    return bindings[0].get("HostPort")


def _current_max_connections(inspect_payload: dict[str, object]) -> str | None:
    cmd = inspect_payload.get("Config", {}).get("Cmd") or []
    for item in cmd:
        if item.startswith("max_connections="):
            return item.split("=", 1)[1]
    return None
