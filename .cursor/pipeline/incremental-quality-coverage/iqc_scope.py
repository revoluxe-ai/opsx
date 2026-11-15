"""Вычисление CONFIGURED / VERIFIED / REQUIRED_CHANGE scope."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from iqc_policy import IQCManifest, configured_py_files, load_manifest, verified_include_set
from iqc_states import FileCoverageState

REPO_ROOT = Path(__file__).resolve().parents[3]


def classify_file(
    rel_path: str,
    *,
    configured: frozenset[str],
    verified: frozenset[str],
    required: frozenset[str],
) -> FileCoverageState:
    """Классифицирует один repo-relative `.py` путь."""
    norm = rel_path.replace("\\", "/").lstrip("./")
    if norm in verified:
        return FileCoverageState.STRICT_COVERED
    if norm in required:
        return FileCoverageState.COVERAGE_EXPANSION
    if norm in configured:
        return FileCoverageState.LEGACY_UNCOVERED
    return FileCoverageState.LEGACY_UNCOVERED


def build_scope_snapshot(root: Path = REPO_ROOT) -> dict[str, Any]:
    """Снимок трёх scope и классификации (read-only)."""
    manifest = load_manifest(root)
    configured = configured_py_files(root, manifest)
    verified = verified_include_set(root, manifest)
    required = frozenset(required_change_paths(root))
    legacy = sorted(configured - verified)
    expansion = sorted(p for p in required if p not in verified)
    return {
        "CONFIGURED_SCOPE": sorted(configured),
        "VERIFIED_SCOPE": sorted(verified),
        "REQUIRED_CHANGE_SCOPE": sorted(required),
        "legacy_uncovered_count": len(legacy),
        "verified_count": len(verified),
        "configured_count": len(configured),
        "expansion_needed": expansion,
        "manifest_activation": manifest.activation.value,
        "manifest_revision": manifest.revision,
        "policy_id": manifest.policy_id,
    }


def required_change_paths(root: Path) -> tuple[str, ...]:
    """In-policy changed `.py` (WORKTREE semantics, shared с pyright coverage)."""
    from iqc_required_change import required_change_paths as _paths  # noqa: WPS433

    return _paths(root)


def paths_needing_expansion(
    required: Sequence[str],
    *,
    configured: frozenset[str],
    verified: frozenset[str],
) -> tuple[str, ...]:
    """Пути в REQUIRED_CHANGE, которые ещё не STRICT_COVERED."""
    out = [p for p in required if p.replace("\\", "/") not in verified]
    return tuple(sorted(out))
