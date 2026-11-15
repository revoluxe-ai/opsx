"""Pyright-доказательство для CANDIDATE_VERIFIED → EVIDENCE_VERIFIED."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Mapping, Sequence

from iqc_policy import IQCManifest, load_pyright_config, verified_include_set
from iqc_states import VerifiedScopeLayer

REPO_ROOT = Path(__file__).resolve().parents[3]


def git_head_sha(root: Path) -> str | None:
    """Текущий HEAD worktree (evidence binding)."""
    git_marker = root / ".git"
    if not (git_marker.is_dir() or git_marker.is_file()):
        return None
    proc = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    return value or None


def verified_path_content_hashes(root: Path, paths: Sequence[str]) -> dict[str, str]:
    """SHA256 содержимого verified candidate paths (binding к git HEAD)."""
    out: dict[str, str] = {}
    for rel in sorted(paths):
        path = root / rel
        if not path.is_file():
            out[rel] = "MISSING"
            continue
        out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def verified_config_fingerprint(root: Path, manifest: IQCManifest) -> str:
    """Digest содержимого verified pyright config (revision-bound evidence)."""
    path = root / manifest.verified_config_rel
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest


def resolve_pyright_executable(root: Path) -> str | None:
    """Pyright из PATH, toolchain root venv, затем target worktree venv."""
    exe = shutil.which("pyright")
    if exe is not None:
        return exe
    for candidate in (
        REPO_ROOT / ".venv/bin/pyright",
        REPO_ROOT / ".venv/Scripts/pyright.exe",
        root / ".venv/bin/pyright",
        root / ".venv/Scripts/pyright.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def pyright_toolchain_fingerprint() -> str | None:
    """Версия pyright из PATH / venv (None если бинарь недоступен)."""
    exe = resolve_pyright_executable(REPO_ROOT)
    if exe is None:
        return None
    proc = subprocess.run(
        [exe, "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    line = (proc.stdout or proc.stderr or "").strip().splitlines()
    return line[0] if line else None


def run_verified_scope_pyright(
    root: Path,
    manifest: IQCManifest,
) -> dict[str, Any]:
    """
    Запускает pyright на verified config (candidate scope).

    Returns:
        exit_code, optional combined output tail, command summary.
    """
    exe = resolve_pyright_executable(root)
    if exe is None:
        return {
            "ran": False,
            "exit_code": None,
            "reason": "PYRIGHT_UNAVAILABLE",
            "command": None,
        }
    rel = manifest.verified_config_rel
    proc = subprocess.run(
        [exe, "--project", rel],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )
    tail = (proc.stdout + proc.stderr)[-4000:]
    return {
        "ran": True,
        "exit_code": proc.returncode,
        "reason": None,
        "command": f"{exe} --project {rel}",
        "output_tail": tail,
    }


def collect_verified_scope_evidence(
    root: Path,
    manifest: IQCManifest,
    *,
    run_pyright: bool | None = None,
) -> dict[str, Any]:
    """
    Различает CONFIGURED listing, CANDIDATE_VERIFIED (config) и EVIDENCE_VERIFIED (pyright OK).

    Без успешного pyright candidate paths не считаются evidence-verified (no auto-90).
    """
    if run_pyright is None:
        run_pyright = os.environ.get("OPSX_IQC_SKIP_PYRIGHT_EVIDENCE", "").strip() != "1"

    candidate_paths = verified_include_set(root, manifest)
    candidate_list = sorted(candidate_paths)
    candidate_count = len(candidate_paths)
    configured_cfg = load_pyright_config(root, manifest.configured_config_rel)
    configured_include = [str(x) for x in configured_cfg.get("include", [])]
    configured_cfg_fp = hashlib.sha256(
        (root / manifest.configured_config_rel).read_bytes(),
    ).hexdigest()

    execution_id = os.environ.get("OPSX_IQC_EXECUTION_ID", "").strip() or str(uuid.uuid4())
    git_head = git_head_sha(root)
    path_hashes = verified_path_content_hashes(root, candidate_list)
    paths_digest = hashlib.sha256(
        json.dumps(path_hashes, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    ).hexdigest()

    binding = {
        "manifest_revision": manifest.revision,
        "policy_id": manifest.policy_id,
        "verified_config_fingerprint": verified_config_fingerprint(root, manifest),
        "configured_config_fingerprint": configured_cfg_fp,
        "verified_config_rel": manifest.verified_config_rel,
        "configured_config_rel": manifest.configured_config_rel,
        "pyright_toolchain_fingerprint": pyright_toolchain_fingerprint(),
        "git_head": git_head,
        "verified_path_content_hashes": path_hashes,
        "verified_paths_content_digest": paths_digest,
        "execution_id": execution_id,
    }

    pyright_result: dict[str, Any] = {"ran": False, "exit_code": None}
    if run_pyright:
        pyright_result = run_verified_scope_pyright(root, manifest)
        binding["pyright_exit_code"] = pyright_result.get("exit_code")
        binding["pyright_command"] = pyright_result.get("command")
    else:
        binding["pyright_exit_code"] = None
        binding["pyright_skipped"] = True

    layer = VerifiedScopeLayer.CANDIDATE_VERIFIED
    evidence_verified_count = 0
    if pyright_result.get("ran") and pyright_result.get("exit_code") == 0:
        layer = VerifiedScopeLayer.EVIDENCE_VERIFIED
        evidence_verified_count = candidate_count
    elif not pyright_result.get("ran") and pyright_result.get("reason") == "PYRIGHT_UNAVAILABLE":
        layer = VerifiedScopeLayer.CANDIDATE_VERIFIED

    payload = {
        "layer": layer.value,
        "CONFIGURED_SCOPE_hint": configured_include,
        "CANDIDATE_VERIFIED_SCOPE_count": candidate_count,
        "EVIDENCE_VERIFIED_SCOPE_count": evidence_verified_count,
        "binding": binding,
        "pyright": pyright_result,
    }
    canonical = json.dumps(
        {
            "revision": manifest.revision,
            "verified_config_fingerprint": binding["verified_config_fingerprint"],
            "configured_config_fingerprint": binding["configured_config_fingerprint"],
            "verified_paths_content_digest": paths_digest,
            "git_head": git_head,
            "execution_id": execution_id,
            "toolchain": binding.get("pyright_toolchain_fingerprint"),
            "exit": binding.get("pyright_exit_code"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    payload["evidence_digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return payload


def evidence_binding_fingerprint(evidence: Mapping[str, Any]) -> str:
    """Digest для supersession (config + toolchain + pyright outcome)."""
    binding = evidence.get("binding") or {}
    canonical = json.dumps(
        {
            "manifest_revision": binding.get("manifest_revision"),
            "verified_config_fingerprint": binding.get("verified_config_fingerprint"),
            "configured_config_fingerprint": binding.get("configured_config_fingerprint"),
            "verified_paths_content_digest": binding.get("verified_paths_content_digest"),
            "git_head": binding.get("git_head"),
            "execution_id": binding.get("execution_id"),
            "pyright_toolchain_fingerprint": binding.get("pyright_toolchain_fingerprint"),
            "pyright_exit_code": binding.get("pyright_exit_code"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
