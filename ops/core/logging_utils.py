from __future__ import annotations

import logging

RESET = "\033[0m"
LEVEL_STYLES = {
    logging.INFO: ("\033[32m", "OK"),
    logging.WARNING: ("\033[33m", "WARN"),
    logging.ERROR: ("\033[31m", "ERROR"),
    logging.CRITICAL: ("\033[31m", "ERROR"),
}


class ColorFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        color, label = LEVEL_STYLES.get(record.levelno, ("", "INFO"))
        record.level_prefix = f"{color}[{label}]{RESET}" if color else f"[{label}]"
        return super().format(record)


def configure_logging() -> None:
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return

    handler = logging.StreamHandler()
    handler.setFormatter(ColorFormatter("%(level_prefix)s %(message)s"))
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)
