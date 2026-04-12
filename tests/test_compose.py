from __future__ import annotations

from pathlib import Path

import yaml

from ops.core.compose import GENERATED_COMMENT, PGDATA_TARGET, generate_compose_text


def test_generate_compose_text_for_single_service(tmp_path: Path) -> None:
    _write_project_files(
        tmp_path,
        global_env="POSTGRES_VERSION=18\n",
        services={
            "test": (
                "POSTGRES_USER=admin\n"
                "POSTGRES_PASSWORD=pa$word\n"
                "POSTGRES_PORT=5401\n"
            )
        },
    )
    meminfo_path = _write_meminfo(tmp_path, memtotal_kb=1_000_000)

    compose_text = generate_compose_text(tmp_path, proc_meminfo_path=meminfo_path)
    header, yaml_text = compose_text.split("\n", 1)
    document = yaml.safe_load(yaml_text)

    assert header == GENERATED_COMMENT
    service = document["services"]["test"]
    assert service["image"] == "postgres:18"
    assert service["container_name"] == "test"
    assert service["env_file"] == [{"path": "./services/.env.test", "format": "raw"}]
    assert service["environment"] == {"POSTGRES_DB": "test"}
    assert service["ports"] == ["5401:5432"]
    assert service["volumes"] == [f"./data/test:{PGDATA_TARGET}"]
    assert service["restart"] == "unless-stopped"
    assert service["healthcheck"] == {
        "test": ["CMD-SHELL", "pg_isready -U $$POSTGRES_USER -d $$POSTGRES_DB"],
        "interval": "10s",
        "timeout": "5s",
        "retries": 3,
        "start_period": "30s",
    }
    assert service["shm_size"] == "921600000"


def test_generate_compose_text_adds_optional_command_and_limits(tmp_path: Path) -> None:
    _write_project_files(
        tmp_path,
        global_env="POSTGRES_VERSION=18\n",
        services={
            "test": (
                "POSTGRES_USER=admin\n"
                "POSTGRES_PASSWORD=secret\n"
                "POSTGRES_PORT=5401\n"
                "POSTGRES_MAX_CONNECTIONS=10000\n"
                "POSTGRES_MEMORY_LIMIT=512M\n"
                "POSTGRES_CPU_LIMIT=1.50\n"
            )
        },
    )
    meminfo_path = _write_meminfo(tmp_path, memtotal_kb=2_000_000)

    compose_text = generate_compose_text(tmp_path, proc_meminfo_path=meminfo_path)
    document = yaml.safe_load(compose_text.split("\n", 1)[1])
    service = document["services"]["test"]

    assert service["command"] == ["postgres", "-c", "max_connections=10000"]
    assert service["deploy"] == {"resources": {"limits": {"memory": "512M", "cpus": "1.50"}}}


def test_generate_compose_text_sorts_services_and_handles_empty_project(tmp_path: Path) -> None:
    _write_project_files(
        tmp_path,
        global_env="POSTGRES_VERSION=18\n",
        services={
            "beta": "POSTGRES_USER=admin\nPOSTGRES_PASSWORD=one\nPOSTGRES_PORT=5402\n",
            "alpha": "POSTGRES_USER=admin\nPOSTGRES_PASSWORD=two\nPOSTGRES_PORT=5401\n",
        },
    )
    meminfo_path = _write_meminfo(tmp_path, memtotal_kb=1000)

    compose_text = generate_compose_text(tmp_path, proc_meminfo_path=meminfo_path)
    document = yaml.safe_load(compose_text.split("\n", 1)[1])
    assert list(document["services"]) == ["alpha", "beta"]
    assert document["services"]["alpha"]["shm_size"] == "460800"
    assert document["services"]["beta"]["shm_size"] == "460800"

    empty_project = tmp_path / "empty"
    empty_project.mkdir()
    (empty_project / ".env").write_text("POSTGRES_VERSION=18\n", encoding="utf-8")
    empty_compose_text = generate_compose_text(empty_project, proc_meminfo_path=meminfo_path)
    empty_document = yaml.safe_load(empty_compose_text.split("\n", 1)[1])
    assert empty_document == {"services": {}}


def _write_project_files(project_dir: Path, *, global_env: str, services: dict[str, str]) -> None:
    (project_dir / ".env").write_text(global_env, encoding="utf-8")
    services_dir = project_dir / "services"
    services_dir.mkdir(exist_ok=True)
    for service_name, content in services.items():
        (services_dir / f".env.{service_name}").write_text(content, encoding="utf-8")


def _write_meminfo(project_dir: Path, *, memtotal_kb: int) -> Path:
    meminfo_path = project_dir / "meminfo"
    meminfo_path.write_text(f"MemTotal:       {memtotal_kb} kB\n", encoding="utf-8")
    return meminfo_path
