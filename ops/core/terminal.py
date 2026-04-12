"""Minimal terminal helpers for Stage 1."""

from __future__ import annotations

# ANSI-коды для цветного вывода в терминал
RESET = "\033[0m"    # Сброс цвета
GREEN = "\033[32m"   # Зелёный — успех
YELLOW = "\033[33m"  # Жёлтый — предупреждение
RED = "\033[31m"     # Красный — ошибка


def print_ok(message: str) -> None:
    """Выводит сообщение об успехе с зелёным префиксом [OK].

    Args:
        message: текст сообщения.
    """
    print(f"{GREEN}[OK]{RESET} {message}")


def print_warn(message: str) -> None:
    """Выводит предупреждение с жёлтым префиксом [WARN].

    Args:
        message: текст предупреждения.
    """
    print(f"{YELLOW}[WARN]{RESET} {message}")


def print_error(message: str) -> None:
    """Выводит ошибку с красным префиксом [ERROR].

    Args:
        message: текст ошибки.
    """
    print(f"{RED}[ERROR]{RESET} {message}")

