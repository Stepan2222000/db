from __future__ import annotations

from pathlib import Path

import pytest

from ops.core.discovery import ServiceDiscoveryError, discover_service_env_files, service_name_from_path


def test_discover_service_env_files_returns_sorted_env_paths(tmp_path: Path) -> None:
    services_dir = tmp_path / "services"
    services_dir.mkdir()
    (services_dir / ".env.beta").write_text("", encoding="utf-8")
    (services_dir / ".env.alpha").write_text("", encoding="utf-8")

    discovered = discover_service_env_files(tmp_path)

    assert [path.name for path in discovered] == [".env.alpha", ".env.beta"]


def test_service_name_from_path_rejects_invalid_names() -> None:
    with pytest.raises(ServiceDiscoveryError):
        service_name_from_path(Path("services/.env.bad-name"))


def test_discover_service_env_files_ignores_missing_services_dir(tmp_path: Path) -> None:
    assert discover_service_env_files(tmp_path) == []

