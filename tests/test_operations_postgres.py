from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ops.core.docker import CommandResult
from ops.core.models import ServiceConfig
from ops.operations import postgres as postgres_ops


def test_wait_for_pg_ready_retries_until_success(monkeypatch: pytest.MonkeyPatch) -> None:
    results = iter(
        [
            CommandResult(stdout="", stderr="", returncode=1),
            CommandResult(stdout="", stderr="", returncode=2),
            CommandResult(stdout="", stderr="", returncode=0),
        ]
    )
    sleep_calls: list[float] = []

    monkeypatch.setattr(
        postgres_ops,
        "docker_exec_capture",
        lambda *args, **kwargs: next(results),
    )
    monkeypatch.setattr(
        postgres_ops.time,
        "monotonic",
        lambda: 0.0,
    )
    monkeypatch.setattr(
        postgres_ops.time,
        "sleep",
        lambda seconds: sleep_calls.append(seconds),
    )

    postgres_ops.wait_for_pg_ready("demo", "demo", "admin", timeout_seconds=10, poll_interval=0.5)

    assert sleep_calls == [0.5, 0.5]


def test_wait_for_pg_ready_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monotonic_values = iter([0.0, 0.1, 0.2, 1.1])

    monkeypatch.setattr(
        postgres_ops,
        "docker_exec_capture",
        lambda *args, **kwargs: CommandResult(stdout="", stderr="", returncode=1),
    )
    monkeypatch.setattr(
        postgres_ops.time,
        "monotonic",
        lambda: next(monotonic_values),
    )
    monkeypatch.setattr(postgres_ops.time, "sleep", lambda seconds: None)

    with pytest.raises(TimeoutError, match="did not become ready"):
        postgres_ops.wait_for_pg_ready("demo", "demo", "admin", timeout_seconds=1, poll_interval=0.1)


def test_sql_helpers_escape_values() -> None:
    assert postgres_ops.sql_literal("pa'ss") == "'pa''ss'"
    assert postgres_ops.sql_identifier('ro"le') == '"ro""le"'


def test_run_psql_uses_stdin_when_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeProcess:
        returncode = 0

        def communicate(self, payload):
            captured["payload"] = payload
            return (b"ok\n", b"")

    def fake_popen(container_name, argv, **kwargs):
        captured["container_name"] = container_name
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(postgres_ops, "docker_exec_popen", fake_popen)

    result = postgres_ops.run_psql(
        "demo",
        "admin",
        "postgres",
        "SELECT 1;\n",
        stdin_sql=True,
    )

    assert captured["container_name"] == "demo"
    assert captured["argv"] == [
        "psql",
        "-v",
        "ON_ERROR_STOP=1",
        "--username",
        "admin",
        "--dbname",
        "postgres",
    ]
    assert captured["kwargs"]["stdin"] == subprocess.PIPE
    assert captured["kwargs"]["interactive"] is True
    assert captured["payload"] == b"SELECT 1;\n"
    assert result.stdout == "ok\n"


def test_query_database_size_parses_scalar_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service_config = ServiceConfig(
        name="demo",
        env_path=tmp_path / "services" / ".env.demo",
        postgres_user="admin",
        postgres_password="secret",
        postgres_port=5401,
    )

    monkeypatch.setattr(
        postgres_ops,
        "run_psql",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="7861951\n",
            stderr="",
        ),
    )

    assert postgres_ops.query_database_size("demo", service_config) == 7861951


def test_query_database_size_rejects_empty_and_non_integer_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service_config = ServiceConfig(
        name="demo",
        env_path=tmp_path / "services" / ".env.demo",
        postgres_user="admin",
        postgres_password="secret",
        postgres_port=5401,
    )

    monkeypatch.setattr(
        postgres_ops,
        "run_psql",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="\n",
            stderr="",
        ),
    )
    with pytest.raises(ValueError, match="empty result"):
        postgres_ops.query_database_size("demo", service_config)

    monkeypatch.setattr(
        postgres_ops,
        "run_psql",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="NaN\n",
            stderr="",
        ),
    )
    with pytest.raises(ValueError, match="non-integer"):
        postgres_ops.query_database_size("demo", service_config)


def test_format_size_gb_uses_fixed_two_decimal_output() -> None:
    assert postgres_ops.format_size_gb(0) == "0.00 GB"
    assert postgres_ops.format_size_gb(1024 ** 3) == "1.00 GB"
    assert postgres_ops.format_size_gb(7861951) == "0.01 GB"


def test_sync_service_password_escapes_password_in_sql(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    service_config = ServiceConfig(
        name="demo",
        env_path=tmp_path / "services" / ".env.demo",
        postgres_user='ad"min',
        postgres_password="pa'ss",
        postgres_port=5401,
    )

    def fake_run_psql(container_name, postgres_user, database, sql, *, stdin_sql=False):
        captured["container_name"] = container_name
        captured["postgres_user"] = postgres_user
        captured["database"] = database
        captured["sql"] = sql
        captured["stdin_sql"] = stdin_sql
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    monkeypatch.setattr(postgres_ops, "run_psql", fake_run_psql)

    postgres_ops.sync_service_password("demo", service_config)

    assert captured["container_name"] == "demo"
    assert captured["postgres_user"] == 'ad"min'
    assert captured["database"] == "postgres"
    assert captured["stdin_sql"] is True
    assert captured["sql"] == 'ALTER ROLE "ad""min" WITH PASSWORD \'pa\'\'ss\';\n'
