"""Parsing and writing of env-style files used by the project."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Mapping

# Допустимый формат имени переменной окружения (буквы, цифры, _)
ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class EnvFileError(ValueError):
    """Raised when an env file cannot be parsed or written safely."""


@dataclass(frozen=True)
class BlankLine:
    """Представляет пустую строку в env-файле."""
    pass


@dataclass(frozen=True)
class CommentLine:
    """Строка-комментарий в env-файле.

    Attrs:
        text: исходный текст строки (включая ``#``).
    """
    text: str


@dataclass(frozen=True)
class PairLine:
    """Строка вида ``KEY=VALUE`` в env-файле.

    Attrs:
        key: имя переменной.
        value: значение (всё после первого ``=``).
    """
    key: str
    value: str


# Объединённый тип для любой строки env-файла
EnvLine = BlankLine | CommentLine | PairLine


@dataclass(frozen=True)
class ParsedEnvFile:
    """Результат парсинга env-файла с сохранением структуры.

    Attrs:
        path: путь к исходному файлу.
        lines: кортеж распознанных строк (сохраняет порядок и форматирование).
        values: словарь ``{KEY: VALUE}`` из всех пар.
    """
    path: Path
    lines: tuple[EnvLine, ...]
    values: dict[str, str]


def parse_env_text(text: str, path: Path) -> ParsedEnvFile:
    """Парсит текст env-файла в структурированное представление.

    Распознаёт пустые строки, комментарии (``#``) и пары ``KEY=VALUE``.
    Проверяет корректность ключей и отсутствие дубликатов.

    Args:
        text: содержимое env-файла.
        path: путь к файлу (для сообщений об ошибках).

    Returns:
        ParsedEnvFile с распознанными строками и словарём значений.

    Raises:
        EnvFileError: при невалидном формате или дубликатах ключей.
    """
    lines: list[EnvLine] = []
    values: dict[str, str] = {}

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped:
            lines.append(BlankLine())
            continue
        if raw_line.lstrip().startswith("#"):
            lines.append(CommentLine(raw_line))
            continue

        if "=" not in raw_line:
            raise EnvFileError(f"{path}:{line_number}: expected KEY=VALUE")

        key_part, value_part = raw_line.split("=", 1)
        key = key_part.strip()
        if not ENV_KEY_RE.fullmatch(key):
            raise EnvFileError(f"{path}:{line_number}: invalid env key '{key}'")
        if key in values:
            raise EnvFileError(f"{path}:{line_number}: duplicate env key '{key}'")

        value = value_part
        values[key] = value
        lines.append(PairLine(key=key, value=value))

    return ParsedEnvFile(path=path, lines=tuple(lines), values=values)


def read_env_file(path: Path) -> ParsedEnvFile:
    """Читает env-файл с диска и парсит его.

    Args:
        path: путь к env-файлу.

    Returns:
        ParsedEnvFile с содержимым файла.

    Raises:
        EnvFileError: если файл не найден или не удаётся распарсить.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise EnvFileError(f"Env file not found: {path}") from exc
    return parse_env_text(text, path)


def render_env_text(
    values: Mapping[str, str],
    *,
    existing: ParsedEnvFile | None = None,
) -> str:
    """Рендерит словарь переменных обратно в текст env-файла.

    Два режима работы:
    - Без ``existing``: создаёт новый файл, ключи отсортированы по алфавиту.
    - С ``existing``: сохраняет оригинальное форматирование (пустые строки,
      комментарии), обновляет существующие ключи и дописывает новые в конец.

    Args:
        values: словарь переменных для записи.
        existing: ранее распарсенный файл (для сохранения форматирования).

    Returns:
        Текст env-файла с завершающим переводом строки.
    """
    serialized_values = {key: _serialize_env_value(value) for key, value in values.items()}

    if existing is None:
        output_lines = [f"{key}={serialized_values[key]}" for key in sorted(serialized_values)]
        return "\n".join(output_lines) + ("\n" if output_lines else "")

    unused_keys = dict(serialized_values)
    output_lines: list[str] = []
    for line in existing.lines:
        if isinstance(line, BlankLine):
            output_lines.append("")
            continue
        if isinstance(line, CommentLine):
            output_lines.append(line.text)
            continue
        if line.key not in serialized_values:
            continue

        output_lines.append(f"{line.key}={serialized_values[line.key]}")
        unused_keys.pop(line.key, None)

    if unused_keys:
        if output_lines and output_lines[-1] != "":
            output_lines.append("")
        output_lines.extend(f"{key}={unused_keys[key]}" for key in sorted(unused_keys))

    return "\n".join(output_lines) + ("\n" if output_lines else "")


def write_env_file(path: Path, values: Mapping[str, str]) -> None:
    """Записывает env-файл на диск, сохраняя форматирование если файл существует.

    Создаёт родительские каталоги при необходимости.

    Args:
        path: путь для записи.
        values: словарь переменных для записи.
    """
    existing = read_env_file(path) if path.exists() else None
    rendered = render_env_text(values, existing=existing)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8")


def _serialize_env_value(value: str) -> str:
    """Валидирует и возвращает значение переменной окружения.

    Args:
        value: значение (должно быть строкой).

    Returns:
        Исходная строка без изменений.

    Raises:
        EnvFileError: если значение не является строкой.
    """
    if isinstance(value, str):
        return value
    raise EnvFileError(f"Env value must be a string, got {type(value).__name__}")

