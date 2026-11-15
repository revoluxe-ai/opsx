"""Документированный byte-pin @ BASE_SHA для hermetic CI без полной git-истории."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from tools.openspec.consultation_baseline._constants import BASE_SHA

_PINNED_ROOT = Path(__file__).resolve().parent / "pinned"
_MANIFEST_PATH = _PINNED_ROOT / "manifest.json"


class PinnedSourceError(RuntimeError):
    """Pinned bundle отсутствует или не соответствует manifest."""


def _load_manifest() -> dict[str, Any]:
    if not _MANIFEST_PATH.is_file():
        raise PinnedSourceError(f"missing pinned manifest: {_MANIFEST_PATH}")
    loaded = json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise PinnedSourceError("pinned manifest root must be a JSON object")
    return loaded


def pinned_manifest_base_sha() -> str:
    """BASE_SHA из manifest.json pinned bundle."""

    manifest = _load_manifest()
    base_sha = manifest.get("base_sha")
    if not isinstance(base_sha, str) or not base_sha:
        raise PinnedSourceError("pinned manifest.base_sha must be a non-empty string")
    return base_sha


def _entry_for_git_path(manifest: dict[str, Any], git_path: str) -> dict[str, Any]:
    sources = manifest.get("sources")
    if not isinstance(sources, list):
        raise PinnedSourceError("pinned manifest.sources must be a list")
    for item in sources:
        if isinstance(item, dict) and item.get("git_path") == git_path:
            return item
    raise PinnedSourceError(f"no pinned entry for git_path={git_path!r}")


def expected_sha256_for_path(git_path: str) -> str | None:
    """SHA-256 hex из ``pinned/manifest.json`` для ``git_path``, если pin задокументирован."""

    try:
        entry = _entry_for_git_path(_load_manifest(), git_path)
    except PinnedSourceError:
        return None
    expected_sha = entry.get("sha256")
    if not isinstance(expected_sha, str) or len(expected_sha) != 64:
        return None
    return expected_sha


def load_pinned_bytes(base_sha: str, git_path: str) -> bytes:
    """Загрузить raw bytes из pinned bundle, сверяя SHA-256 с manifest.

    Args:
        base_sha: ожидаемый pin (должен совпасть с manifest.base_sha).
        git_path: логический vendored путь (posix), ключ в manifest.sources.

    Returns:
        Точные байты файла @ BASE_SHA.

    Raises:
        PinnedSourceError: неверный pin, отсутствующий файл или hash mismatch.
    """

    manifest = _load_manifest()
    manifest_sha = manifest.get("base_sha")
    if base_sha != manifest_sha:
        raise PinnedSourceError(
            f"pinned bundle is for base_sha={manifest_sha!r}, requested {base_sha!r}"
        )
    entry = _entry_for_git_path(manifest, git_path)
    expected_sha = entry.get("sha256")
    if not isinstance(expected_sha, str) or len(expected_sha) != 64:
        raise PinnedSourceError(f"invalid sha256 in manifest for {git_path}")

    blob_path = _PINNED_ROOT / git_path
    if not blob_path.is_file():
        raise PinnedSourceError(f"pinned file missing on disk: {blob_path}")

    data = blob_path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected_sha:
        raise PinnedSourceError(
            f"pinned bytes sha256 mismatch for {git_path}: manifest={expected_sha} actual={digest}"
        )
    return data


def git_object_available(repo_root: Path, base_sha: str, git_path: str) -> bool:
    """Проверить, что ``git cat-file`` видит blob @ base_sha без checkout."""

    spec = f"{base_sha}:{git_path}"
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "cat-file", "-e", spec],
        capture_output=True,
        check=False,
    )
    return proc.returncode == 0
