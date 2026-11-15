"""Загрузка и проверка cutover-baseline для STRICT_ON_TOUCH.

Baseline фиксирует SHA cutover, список legacy-файлов в ``ignore`` и
детерминированный fingerprint; guard отклоняет подмену baseline или
повторное добавление путей в ignore.
"""

from __future__ import annotations

from opsx.project_context import resource_path

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BASELINE_SCHEMA_VERSION = "stroy-strict-cutover-baseline-v1"
DEFAULT_BASELINE_REL = Path("tools/pyright/baselines/stroy-strict-cutover-v1.json")


class BaselineError(Exception):
    """Некорректный или повреждённый baseline (fail-closed)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class StrictCutoverBaseline:
    """Снимок cutover для политики strict-on-touch."""

    path: Path
    schema_version: str
    cutover_git_sha: str
    cutover_date_utc: str
    pyright_version: str
    branch: str
    legacy_ignored_files: tuple[str, ...]
    fingerprint: str
    inventory: dict[str, Any]

    @property
    def legacy_ignored_set(self) -> frozenset[str]:
        return frozenset(self.legacy_ignored_files)


def _fingerprint_payload(document: dict[str, Any]) -> dict[str, Any]:
    cutover = document.get("cutover")
    if not isinstance(cutover, dict):
        raise BaselineError("MALFORMED_BASELINE", "cutover must be a mapping")
    legacy = document.get("legacy_ignored_files")
    if not isinstance(legacy, list) or not all(isinstance(x, str) for x in legacy):
        raise BaselineError("MALFORMED_BASELINE", "legacy_ignored_files must be a string list")
    return {
        "schema_version": document.get("schema_version"),
        "cutover_git_sha": cutover.get("git_sha"),
        "pyright_version": cutover.get("pyright_version"),
        "legacy_ignored_files": sorted(legacy),
    }


def compute_fingerprint(document: dict[str, Any]) -> str:
    """Вычисляет fingerprint baseline (без поля ``fingerprint`` в документе)."""
    payload = _fingerprint_payload(document)
    if payload["schema_version"] != BASELINE_SCHEMA_VERSION:
        raise BaselineError(
            "UNSUPPORTED_SCHEMA",
            f"expected schema {BASELINE_SCHEMA_VERSION!r}, got {payload['schema_version']!r}",
        )
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def load_baseline(repo_root: Path, baseline_rel: Path | None = None) -> StrictCutoverBaseline:
    """Читает baseline с диска и проверяет fingerprint."""
    rel = baseline_rel or DEFAULT_BASELINE_REL
    path = (resource_path(repo_root, rel)).resolve()
    if not path.is_file():
        raise BaselineError("MISSING_BASELINE", f"baseline not found: {rel}")

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise BaselineError("MALFORMED_BASELINE", f"invalid JSON in {rel}: {error}") from error

    if not isinstance(document, dict):
        raise BaselineError("MALFORMED_BASELINE", "baseline root must be a mapping")

    stored_fp = document.get("fingerprint")
    if not isinstance(stored_fp, str) or not stored_fp.startswith("sha256:"):
        raise BaselineError("MALFORMED_BASELINE", "fingerprint must be sha256:…")

    expected_fp = compute_fingerprint(document)
    if stored_fp != expected_fp:
        raise BaselineError(
            "BASELINE_TAMPERED",
            f"fingerprint mismatch for {rel}: stored {stored_fp}, expected {expected_fp}",
        )

    cutover = document.get("cutover")
    if not isinstance(cutover, dict):
        raise BaselineError("MALFORMED_BASELINE", "cutover must be a mapping")

    legacy_raw = document.get("legacy_ignored_files")
    if not isinstance(legacy_raw, list):
        raise BaselineError("MALFORMED_BASELINE", "legacy_ignored_files must be a list")

    legacy_sorted = tuple(sorted(str(x).replace("\\", "/") for x in legacy_raw))
    inventory = document.get("inventory")
    if not isinstance(inventory, dict):
        inventory = {}

    return StrictCutoverBaseline(
        path=path,
        schema_version=str(document.get("schema_version", "")),
        cutover_git_sha=str(cutover.get("git_sha", "")),
        cutover_date_utc=str(cutover.get("date_utc", "")),
        pyright_version=str(cutover.get("pyright_version", "")),
        branch=str(cutover.get("branch", "")),
        legacy_ignored_files=legacy_sorted,
        fingerprint=stored_fp,
        inventory=inventory,
    )
