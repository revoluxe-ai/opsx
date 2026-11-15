"""Чтение raw bytes vendored ux_state_contract.json из documented byte pin."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from tools.openspec.consultation_baseline._constants import BASE_SHA, CONTRACT_GIT_PATH
from tools.openspec.consultation_baseline._pinned_sources import (
    PinnedSourceError,
    load_pinned_bytes,
)
from tools.openspec.consultation_baseline._source_errors import SourceLoadError


def sha256_bytes(data: bytes) -> str:
    """SHA-256 hex digest для точных байтов файла."""

    return hashlib.sha256(data).hexdigest()


def load_contract_bytes(repo_root: Path, base_sha: str = BASE_SHA) -> bytes:
    """Загрузить raw bytes контракта из documented byte pin (``pinned/`` bundle).

    Сверка целостности: SHA-256 файла на диске должен совпасть с ``pinned/manifest.json``
  (см. ``load_pinned_bytes``). Git и ``git show`` не используются.

    Args:
        repo_root: корень репозитория (совместимость API; pin читается из пакета tools).
        base_sha: ожидаемый historical pin (должен совпасть с manifest.base_sha).

    Returns:
        Точные байты vendored контракта @ pin.

    Raises:
        SourceLoadError: pin отсутствует, повреждён или не совпадает с manifest.
    """

    _ = repo_root
    try:
        return load_pinned_bytes(base_sha, CONTRACT_GIT_PATH)
    except PinnedSourceError as exc:
        raise SourceLoadError(
            f"documented byte pin unavailable at {base_sha}:{CONTRACT_GIT_PATH}: {exc}"
        ) from exc


def parse_contract(data: bytes) -> dict[str, Any]:
    """Разобрать JSON контракта из raw bytes (без re-serialize для hash)."""

    try:
        loaded = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceLoadError(f"contract JSON parse failed: {exc}") from exc
    if not isinstance(loaded, dict):
        raise SourceLoadError("contract root must be a JSON object")
    states = loaded.get("states")
    if not isinstance(states, dict):
        raise SourceLoadError("contract.states must be a JSON object")
    return loaded
