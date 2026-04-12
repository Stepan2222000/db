"""System information helpers."""

from __future__ import annotations

from pathlib import Path
import math
import re

# Регулярка для извлечения значения MemTotal из /proc/meminfo
MEMTOTAL_RE = re.compile(r"^MemTotal:\s+(\d+)\s+kB$")


class SystemInfoError(ValueError):
    """Raised when system-derived values cannot be read."""


def read_memtotal_bytes(proc_meminfo_path: Path = Path("/proc/meminfo")) -> int:
    """Читает общий объём оперативной памяти из ``/proc/meminfo``.

    Args:
        proc_meminfo_path: путь к файлу meminfo (по умолчанию ``/proc/meminfo``).

    Returns:
        Объём памяти в байтах.

    Raises:
        SystemInfoError: если файл не найден или строка MemTotal отсутствует.
    """
    try:
        lines = proc_meminfo_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise SystemInfoError(f"Missing meminfo file: {proc_meminfo_path}") from exc

    for line in lines:
        match = MEMTOTAL_RE.match(line.strip())
        if match:
            return int(match.group(1)) * 1024

    raise SystemInfoError(f"MemTotal not found in {proc_meminfo_path}")


def compute_shm_size_bytes(memtotal_bytes: int, service_count: int) -> int:
    """Вычисляет размер shared memory (shm_size) для одного контейнера.

    Формула: ``floor(memtotal * 0.9 / service_count)`` —
    90% памяти делится поровну между всеми сервисами.

    Args:
        memtotal_bytes: общий объём памяти системы в байтах.
        service_count: количество сервисов (должно быть > 0).

    Returns:
        Размер shm_size в байтах.

    Raises:
        SystemInfoError: если ``service_count <= 0``.
    """
    if service_count <= 0:
        raise SystemInfoError("service_count must be positive to compute shm_size")
    return math.floor(memtotal_bytes * 0.9 / service_count)

