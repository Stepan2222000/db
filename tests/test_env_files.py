from __future__ import annotations

from pathlib import Path

import pytest

from ops.core.env_files import EnvFileError, parse_env_text, render_env_text, write_env_file


def test_parse_env_text_keeps_comments_blank_lines_and_raw_values() -> None:
    parsed = parse_env_text(
        "# global\n\nPOSTGRES_PASSWORD=pa$word\nPOSTGRES_USER=\"admin\"\n",
        Path("services/.env.test"),
    )

    assert parsed.values == {
        "POSTGRES_PASSWORD": "pa$word",
        "POSTGRES_USER": '"admin"',
    }
    assert len(parsed.lines) == 4


def test_render_env_text_preserves_comments_blank_lines_and_updates_pairs() -> None:
    existing = parse_env_text(
        "# comment\n\nPOSTGRES_USER=admin\nPOSTGRES_PASSWORD=old\n",
        Path("services/.env.test"),
    )

    rendered = render_env_text(
        {
            "POSTGRES_USER": "admin",
            "POSTGRES_PASSWORD": "new$pass",
            "POSTGRES_PORT": "5401",
        },
        existing=existing,
    )

    assert rendered == (
        "# comment\n\nPOSTGRES_USER=admin\nPOSTGRES_PASSWORD=new$pass\n\nPOSTGRES_PORT=5401\n"
    )


def test_parse_env_text_rejects_duplicate_keys() -> None:
    with pytest.raises(EnvFileError):
        parse_env_text(
            "POSTGRES_USER=admin\nPOSTGRES_USER=other\n",
            Path("services/.env.test"),
        )


def test_write_env_file_roundtrip_preserves_comments_and_blank_lines(tmp_path: Path) -> None:
    env_path = tmp_path / "services" / ".env.test"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("# comment\n\nPOSTGRES_USER=admin\nPOSTGRES_PASSWORD=secret\n", encoding="utf-8")

    write_env_file(
        env_path,
        {
            "POSTGRES_USER": "admin",
            "POSTGRES_PASSWORD": "new-secret",
            "POSTGRES_PORT": "5401",
        },
    )

    assert env_path.read_text(encoding="utf-8") == (
        "# comment\n\nPOSTGRES_USER=admin\nPOSTGRES_PASSWORD=new-secret\n\nPOSTGRES_PORT=5401\n"
    )

