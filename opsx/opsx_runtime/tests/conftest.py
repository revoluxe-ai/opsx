"""Точка входа pytest для всего OPSX runtime.

Команда ``poetry run pytest opsx/opsx_runtime/tests/`` по умолчанию
собирала бы только три модуля в этом каталоге. Хук ниже добавляет
``workflows/`` и ``agents/`` (все вложенные ``**/tests/test_*.py``).
"""

from __future__ import annotations

from pathlib import Path

_TESTS_ENTRY = Path(__file__).resolve().parent
_RUNTIME_ROOT = _TESTS_ENTRY.parent
_EXTRA_COLLECTION_ROOTS = (
    _RUNTIME_ROOT / "workflows",
    _RUNTIME_ROOT / "agents",
)


def _positional_cli_paths(args: list[str]) -> list[Path]:
    paths: list[Path] = []
    for raw in args:
        if not raw or raw.startswith("-"):
            continue
        try:
            paths.append(Path(raw).resolve())
        except OSError:
            continue
    return paths


def _is_runtime_tests_entrypoint(args: list[str]) -> bool:
    positional = _positional_cli_paths(args)
    return len(positional) == 1 and positional[0] == _TESTS_ENTRY


def pytest_cmdline_main(config) -> int | None:
    """Расширить collection roots, если CLI задан только как ``.../tests/``."""
    if not _is_runtime_tests_entrypoint(list(config.args)):
        return None
    for extra in _EXTRA_COLLECTION_ROOTS:
        extra_s = str(extra.resolve())
        if extra.is_dir() and extra_s not in config.args:
            config.args.append(extra_s)
    return None
