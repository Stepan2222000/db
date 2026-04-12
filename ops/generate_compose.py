"""Manual Stage 1 entrypoint for compose generation."""

from __future__ import annotations

import argparse
from pathlib import Path

from .core.compose import write_compose_file
from .core.terminal import print_ok


def main() -> int:
    """Точка входа CLI — парсит аргументы и запускает генерацию compose.yaml.

    Возвращает 0 при успешной генерации.
    """
    parser = argparse.ArgumentParser(description="Generate compose.yaml from project env files.")
    parser.add_argument(
        "--project-dir",
        default=".",
        help="Path to the project root. Defaults to the current working directory.",
    )
    parser.add_argument(
        "--proc-meminfo-path",
        default="/proc/meminfo",
        help="Path to the meminfo file used to compute shm_size.",
    )
    args = parser.parse_args()

    compose_path = write_compose_file(
        Path(args.project_dir).resolve(),
        proc_meminfo_path=Path(args.proc_meminfo_path),
    )
    print_ok(f"Generated {compose_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

