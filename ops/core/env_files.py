from __future__ import annotations

from pathlib import Path
from typing import Mapping


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}

    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            stripped_line = raw_line.strip()
            if not stripped_line or stripped_line.startswith("#"):
                continue

            if "=" not in raw_line:
                raise ValueError(f"{path}:{line_number}: expected KEY=VALUE")

            key, value = raw_line.split("=", 1)
            key = key.strip()
            if not key:
                raise ValueError(f"{path}:{line_number}: empty key")

            values[key] = value.strip()

    return values


def write_env_file(path: Path, values: Mapping[str, str]) -> None:
    lines = [f"{key}={value}" for key, value in values.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
