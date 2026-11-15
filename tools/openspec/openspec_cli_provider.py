"""Проверка TARGET-local OpenSpec CLI provider (pinned @fission-ai/openspec).

Отдельный от ``openspec_guard.py``: guard — бюджеты размеров; provider — наличие
и версия Node CLI без npx/global fallback.
"""

from __future__ import annotations

from opsx.project_context import resource_path

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROVIDER_MANIFEST_REL = "opsx_tools/openspec-cli-node/provider.manifest.json"
LOCKFILE_REL = "opsx_tools/openspec-cli-node/package-lock.json"
PACKAGE_JSON_REL = "opsx_tools/openspec-cli-node/package.json"
WRAPPER_REL = "opsx_tools/openspec-cli"
BINARY_REL = "opsx_tools/openspec-cli-node/node_modules/.bin/openspec"
EXPECTED_PACKAGE_VERSION = "1.14.1"


def _openspec_version_argv(binary_path: Path) -> list[str]:
    """Argv для ``openspec --version`` с учётом npm shims на Windows."""

    if sys.platform == "win32":
        cmd = binary_path.with_suffix(".cmd")
        if cmd.is_file():
            return [str(cmd)]
    return [str(binary_path)]


@dataclass(frozen=True)
class ProviderCheckResult:
    """Результат одной проверки provider."""

    ok: bool
    code: str
    detail: str


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def validate_openspec_cli_provider(root: Path) -> list[ProviderCheckResult]:
    """Проверить manifest, lockfile pin и установленный бинарник (без сети)."""

    findings: list[ProviderCheckResult] = []
    manifest_path = resource_path(root, PROVIDER_MANIFEST_REL)
    manifest = _read_json(manifest_path)
    if manifest is None:
        findings.append(
            ProviderCheckResult(
                ok=False,
                code="MISSING_PROVIDER_MANIFEST",
                detail=str(PROVIDER_MANIFEST_REL),
            )
        )
        return findings

    declared_version = str(manifest.get("package_version", ""))
    if declared_version != EXPECTED_PACKAGE_VERSION:
        findings.append(
            ProviderCheckResult(
                ok=False,
                code="MANIFEST_VERSION_DRIFT",
                detail=f"expected {EXPECTED_PACKAGE_VERSION}, manifest has {declared_version}",
            )
        )

    package_json = _read_json(resource_path(root, PACKAGE_JSON_REL))
    if package_json is None:
        findings.append(
            ProviderCheckResult(
                ok=False,
                code="MISSING_PACKAGE_JSON",
                detail=PACKAGE_JSON_REL,
            )
        )
    else:
        deps = package_json.get("dependencies") or {}
        pinned = deps.get("@fission-ai/openspec")
        if pinned != EXPECTED_PACKAGE_VERSION:
            findings.append(
                ProviderCheckResult(
                    ok=False,
                    code="PACKAGE_JSON_VERSION_DRIFT",
                    detail=f"expected {EXPECTED_PACKAGE_VERSION}, package.json has {pinned!r}",
                )
            )

    lock_path = resource_path(root, LOCKFILE_REL)
    if not lock_path.is_file():
        findings.append(
            ProviderCheckResult(
                ok=False,
                code="MISSING_LOCKFILE",
                detail=LOCKFILE_REL,
            )
        )

    wrapper_path = resource_path(root, WRAPPER_REL)
    if not wrapper_path.is_file():
        findings.append(
            ProviderCheckResult(
                ok=False,
                code="MISSING_WRAPPER",
                detail=WRAPPER_REL,
            )
        )

    binary_path = resource_path(root, BINARY_REL)
    if not binary_path.is_file():
        findings.append(
            ProviderCheckResult(
                ok=False,
                code="PROVIDER_NOT_INSTALLED",
                detail=f"missing {BINARY_REL}; run opsx_tools/openspec-cli-node/setup.sh",
            )
        )
        return findings

    try:
        proc = subprocess.run(
            [*_openspec_version_argv(binary_path), "--version"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        findings.append(
            ProviderCheckResult(
                ok=False,
                code="PROVIDER_EXEC_ERROR",
                detail=str(error),
            )
        )
        return findings

    version_out = (proc.stdout or proc.stderr or "").strip().splitlines()
    reported = version_out[-1].strip() if version_out else ""
    if proc.returncode != 0 or reported != EXPECTED_PACKAGE_VERSION:
        findings.append(
            ProviderCheckResult(
                ok=False,
                code="PROVIDER_VERSION_MISMATCH",
                detail=f"expected {EXPECTED_PACKAGE_VERSION}, got rc={proc.returncode} output={reported!r}",
            )
        )

    if not findings:
        findings.append(
            ProviderCheckResult(ok=True, code="OK", detail=EXPECTED_PACKAGE_VERSION)
        )
    return findings


def provider_identity(root: Path) -> str | None:
    """SHA256 lockfile для привязки identity (read-only)."""

    lock_path = resource_path(root, LOCKFILE_REL)
    if not lock_path.is_file():
        return None
    import hashlib

    return hashlib.sha256(lock_path.read_bytes()).hexdigest()
