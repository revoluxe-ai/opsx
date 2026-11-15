"""Чтение Python-исходников invest_yalta из documented byte pin (``pinned/`` bundle)."""

from __future__ import annotations

from pathlib import Path

from tools.openspec.consultation_baseline._pinned_sources import (
    PinnedSourceError,
    load_pinned_bytes,
)
from tools.openspec.consultation_baseline._source_errors import SourceLoadError


def load_python_source(repo_root: Path, base_sha: str, git_path: str) -> str:
    """Вернуть текст модуля из documented byte pin @ ``base_sha``.

    Args:
        repo_root: корень репозитория (совместимость API; pin читается из пакета tools).
        base_sha: полный или короткий SHA pin (должен совпасть с ``pinned/manifest.json``).
        git_path: логический путь vendored файла (posix), ключ в manifest.sources.

    Returns:
        Декодированный UTF-8 исходник.

    Raises:
        SourceLoadError: pin отсутствует, повреждён или не совпадает с manifest.
    """

    _ = repo_root
    try:
        return load_pinned_bytes(base_sha, git_path).decode("utf-8")
    except PinnedSourceError as exc:
        raise SourceLoadError(
            f"documented byte pin unavailable at {base_sha}:{git_path}: {exc}"
        ) from exc
