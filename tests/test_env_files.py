from __future__ import annotations

from pathlib import Path

from ops.core.env_files import read_env_file, write_env_file


def test_read_env_file_ignores_comments_and_blank_lines(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n"
        "# comment\n"
        "POSTGRES_USER=admin\n"
        "\n"
        "POSTGRES_PASSWORD=pa$word\n",
        encoding="utf-8",
    )

    assert read_env_file(env_path) == {
        "POSTGRES_USER": "admin",
        "POSTGRES_PASSWORD": "pa$word",
    }


def test_write_env_file_writes_canonical_key_value_lines(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"

    write_env_file(
        env_path,
        {
            "POSTGRES_USER": "admin",
            "POSTGRES_PASSWORD": "pa$word",
        },
    )

    assert env_path.read_text(encoding="utf-8") == (
        "POSTGRES_USER=admin\n"
        "POSTGRES_PASSWORD=pa$word\n"
    )
