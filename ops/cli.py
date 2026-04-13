from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import typer

from ops.commands.add import add as add_command
from ops.commands.apply import apply as apply_command
from ops.commands.autobackup import autobackup as autobackup_command
from ops.commands.backup import backup as backup_command
from ops.commands.dump import dump as dump_command
from ops.commands.remove import remove as remove_command
from ops.commands.restore import restore as restore_command
from ops.commands.sizes import sizes as sizes_command
from ops.core.compose import build_compose, write_compose
from ops.core.config import load_global_config, load_service_config
from ops.core.discovery import discover_services
from ops.core.logging_utils import configure_logging

LOGGER = logging.getLogger(__name__)
app = typer.Typer(no_args_is_help=True)

app.command()(add_command)
app.command()(remove_command)
app.command()(apply_command)
app.command()(backup_command)
app.command()(dump_command)
app.command()(restore_command)
app.command()(sizes_command)
app.command()(autobackup_command)


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
    try:
        app(
            prog_name="db",
            args=list(argv) if argv is not None else None,
            standalone_mode=False,
        )
    except SystemExit as exc:
        return int(exc.code)
    except Exception:
        LOGGER.exception("Command failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
