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
from ops.commands.autobackup import app as autobackup_app
from ops.commands.backup import backup as backup_command
from ops.commands.dump import dump as dump_command
from ops.commands.remove import remove as remove_command
from ops.commands.restore import restore as restore_command
from ops.commands.sizes import sizes as sizes_command
from ops.core.logging_utils import configure_logging
from ops.operations.services import generate_compose

LOGGER = logging.getLogger(__name__)
app = typer.Typer(no_args_is_help=True)

app.command()(add_command)
app.command()(remove_command)
app.command()(apply_command)
app.command()(backup_command)
app.command()(dump_command)
app.command()(restore_command)
app.command()(sizes_command)
app.add_typer(autobackup_app, name="autobackup")


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
