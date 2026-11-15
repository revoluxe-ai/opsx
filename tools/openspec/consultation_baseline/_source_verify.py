"""Сверка vendored bytes с documented pin (делегируется ``load_pinned_bytes``).

Исторические исходники consultation-funnel-baseline читаются только из
``tools/openspec/consultation_baseline/pinned/``. Fail-closed проверка —
SHA-256 байтов файла против ``pinned/manifest.json`` внутри ``load_pinned_bytes``.
Этот модуль оставлен как якорь доменной политики (без git show / BASE_SHA lookup).
"""

from __future__ import annotations

from tools.openspec.consultation_baseline._pinned_sources import (
    PinnedSourceError,
    load_pinned_bytes,
)
from tools.openspec.consultation_baseline._source_errors import SourceLoadError


def assert_documented_pin_bytes(base_sha: str, git_path: str) -> bytes:
    """Загрузить байты pin и неявно проверить их против manifest (fail-closed).

    Args:
        base_sha: ожидаемый pin revision.
        git_path: vendored путь (posix) из manifest.sources.

    Returns:
        Проверенные raw bytes.

    Raises:
        SourceLoadError: pin отсутствует или hash mismatch.
    """

    try:
        return load_pinned_bytes(base_sha, git_path)
    except PinnedSourceError as exc:
        raise SourceLoadError(
            f"documented byte pin verification failed for {base_sha}:{git_path}: {exc}"
        ) from exc
