from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Sequence

from ops.core.compose import build_compose, write_compose
from ops.core.config import load_global_config, load_service_config
from ops.core.discovery import discover_services
from ops.core.logging_utils import configure_logging

LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="db")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "generate-compose",
        help="Generate compose.yaml from .env and services/.env.*",
    )
    return parser


def generate_compose(project_root: Path) -> Path:
    global_config = load_global_config(project_root)
    service_names = discover_services(project_root)
    if not service_names:
        raise ValueError(
            f"{project_root / 'services'}: no service configuration files found"
        )

    service_configs = [
        load_service_config(project_root, service_name)
        for service_name in service_names
    ]
    compose_dict = build_compose(project_root, global_config, service_configs)
    compose_path = write_compose(project_root, compose_dict)

    LOGGER.info("Wrote %s for %d service(s)", compose_path, len(service_configs))
    return compose_path


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "generate-compose":
            generate_compose(Path.cwd())
            return 0
    except Exception:
        LOGGER.exception("Command failed")
        return 1

    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
