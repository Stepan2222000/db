from __future__ import annotations

from pathlib import Path

import pytest

from ops.core.models import GlobalConfig, ServiceConfig
from ops.operations import services as services_ops


def test_pick_service_port_autopicks_from_5401(tmp_path: Path) -> None:
    configs = [
        ServiceConfig(
            name="alpha",
            env_path=tmp_path / "services" / ".env.alpha",
            postgres_user="admin",
            postgres_password="secret",
            postgres_port=5401,
        ),
        ServiceConfig(
            name="beta",
            env_path=tmp_path / "services" / ".env.beta",
            postgres_user="admin",
            postgres_password="secret",
            postgres_port=5402,
        ),
    ]

    assert services_ops.pick_service_port(configs) == 5403


def test_pick_service_port_rejects_invalid_and_conflicting_requested_port(
    tmp_path: Path,
) -> None:
    configs = [
        ServiceConfig(
            name="alpha",
            env_path=tmp_path / "services" / ".env.alpha",
            postgres_user="admin",
            postgres_password="secret",
            postgres_port=5401,
        ),
    ]

    with pytest.raises(ValueError, match="1024..65535"):
        services_ops.pick_service_port(configs, 80)
    with pytest.raises(ValueError, match="already in use"):
        services_ops.pick_service_port(configs, 5401)


def test_resolve_add_password_prefers_arg_then_global() -> None:
    global_config = GlobalConfig(
        postgres_version="18",
        default_postgres_password="from-global",
    )

    assert services_ops.resolve_add_password(global_config, "from-arg") == "from-arg"
    assert services_ops.resolve_add_password(global_config, None) == "from-global"


def test_resolve_add_password_requires_one_source() -> None:
    global_config = GlobalConfig(
        postgres_version="18",
        default_postgres_password=None,
    )

    with pytest.raises(ValueError, match="Password is required"):
        services_ops.resolve_add_password(global_config, None)


def test_summarize_apply_changes_reports_new_service_and_created_data_dir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    compose_path = tmp_path / "compose.yaml"
    compose_path.write_text("new compose\n", encoding="utf-8")
    service_config = ServiceConfig(
        name="demo",
        env_path=tmp_path / "services" / ".env.demo",
        postgres_user="admin",
        postgres_password="secret",
        postgres_port=5401,
    )

    monkeypatch.setattr(services_ops, "container_exists", lambda name: False)

    summary = services_ops.summarize_apply_changes(
        tmp_path,
        [service_config],
        previous_compose_text=None,
        created_data_dirs={"demo"},
    )

    assert "compose.yaml: created" in summary
    assert "service demo: will be created" in summary
    assert "data dir: created" in summary


def test_summarize_apply_changes_reports_runtime_differences(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    previous_compose = "# old\nservices:\n  demo: {}\n"
    new_compose = "# new\nservices:\n  demo:\n    image: postgres:18\n"
    (tmp_path / "compose.yaml").write_text(new_compose, encoding="utf-8")
    service_config = ServiceConfig(
        name="demo",
        env_path=tmp_path / "services" / ".env.demo",
        postgres_user="new_admin",
        postgres_password="new_secret",
        postgres_port=5402,
        max_connections=200,
        memory_limit="512M",
        cpu_limit="1.50",
    )

    monkeypatch.setattr(services_ops, "container_exists", lambda name: True)
    monkeypatch.setattr(services_ops, "container_is_running", lambda name: False)
    monkeypatch.setattr(
        services_ops,
        "docker_inspect_json",
        lambda name: {
            "Config": {
                "Env": [
                    "POSTGRES_USER=old_admin",
                    "POSTGRES_PASSWORD=old_secret",
                    "POSTGRES_MEMORY_LIMIT=256M",
                    "POSTGRES_CPU_LIMIT=1.00",
                ],
                "Cmd": ["postgres", "-c", "max_connections=100"],
            },
            "HostConfig": {
                "PortBindings": {
                    "5432/tcp": [{"HostPort": "5401"}],
                }
            },
            "State": {"Running": False},
        },
    )

    summary = services_ops.summarize_apply_changes(
        tmp_path,
        [service_config],
        previous_compose_text=previous_compose,
    )

    assert "compose.yaml: changed" in summary
    assert "service demo: stopped" in summary
    assert "user: old_admin -> new_admin" in summary
    assert "password: changed" in summary
    assert "port: 5401 -> 5402" in summary
    assert "max_connections: 100 -> 200" in summary
    assert "resources: changed" in summary
