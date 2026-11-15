"""Загрузка manifest и monotonic expansion guard."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from iqc_glob import path_hit_exclude
from iqc_paths import MANIFEST_ACTIVE_REL, MANIFEST_PROPOSED_REL, REPO_ROOT
from iqc_states import ManifestActivation


class IQCPolicyError(Exception):
    """Нарушение политики IQC (fail-closed)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class IQCManifest:
    """Разобранный manifest incremental quality coverage."""

    path: Path
    raw: Mapping[str, Any]
    activation: ManifestActivation
    revision: int
    configured_config_rel: str
    verified_config_rel: str

    @property
    def policy_id(self) -> str:
        return str(self.raw.get("policy_id", ""))


def resolve_manifest_path(root: Path = REPO_ROOT) -> Path | None:
    """ACTIVE manifest приоритетнее PROPOSED bootstrap."""
    active = root / MANIFEST_ACTIVE_REL
    if active.is_file():
        return active
    proposed = root / MANIFEST_PROPOSED_REL
    if proposed.is_file():
        return proposed
    return None


def load_manifest(root: Path = REPO_ROOT) -> IQCManifest:
    """Читает manifest; отсутствие → BLOCKED."""
    path = resolve_manifest_path(root)
    if path is None:
        raise IQCPolicyError("MANIFEST_MISSING", "incremental quality manifest not found")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise IQCPolicyError("MANIFEST_MALFORMED", "manifest root must be a mapping")
    activation_raw = str(data.get("activation", "")).upper()
    try:
        activation = ManifestActivation(activation_raw)
    except ValueError:
        raise IQCPolicyError("MANIFEST_MALFORMED", f"unknown activation: {activation_raw}") from None
    configured = data.get("configured_scope")
    verified = data.get("verified_scope")
    if not isinstance(configured, dict) or not isinstance(verified, dict):
        raise IQCPolicyError("MANIFEST_MALFORMED", "configured_scope/verified_scope required")
    return IQCManifest(
        path=path,
        raw=data,
        activation=activation,
        revision=int(data.get("revision", 0)),
        configured_config_rel=str(configured.get("config_path", "")),
        verified_config_rel=str(verified.get("config_path", "")),
    )


def load_pyright_config(root: Path, rel: str) -> dict[str, Any]:
    path = root / rel
    if not path.is_file():
        raise IQCPolicyError("CONFIG_MISSING", f"missing pyright config: {rel}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise IQCPolicyError("CONFIG_MALFORMED", f"invalid json root: {rel}")
    return data


def verified_include_set(root: Path, manifest: IQCManifest) -> frozenset[str]:
    cfg = load_pyright_config(root, manifest.verified_config_rel)
    include = [str(x) for x in cfg.get("include", [])]
    out: set[str] = set()
    for entry in include:
        norm = entry.replace("\\", "/").lstrip("./")
        if norm.endswith(".py"):
            out.add(norm)
        else:
            base = root / norm
            if base.is_dir():
                for py in base.rglob("*.py"):
                    out.add(str(py.relative_to(root)).replace("\\", "/"))
            elif (root / norm).is_file() and norm.endswith(".py"):
                out.add(norm)
    return frozenset(sorted(out))


def configured_py_files(root: Path, manifest: IQCManifest) -> frozenset[str]:
    """Все in-policy `.py` в CONFIGURED_SCOPE (include − ignore − exclude)."""
    cfg = load_pyright_config(root, manifest.configured_config_rel)
    include = [str(x) for x in cfg.get("include", [])]
    exclude = [str(x) for x in cfg.get("exclude", [])]
    ignore = [str(x) for x in cfg.get("ignore", [])]
    candidates: set[str] = set()
    for inc in include:
        norm = inc.replace("\\", "/").lstrip("./")
        base = root / norm
        if base.is_dir():
            for py in base.rglob("*.py"):
                rel = str(py.relative_to(root)).replace("\\", "/")
                candidates.add(rel)
        elif norm.endswith(".py") and (root / norm).is_file():
            candidates.add(norm)
    filtered: set[str] = set()
    for rel in candidates:
        if path_hit_ignore(rel, ignore):
            continue
        if path_hit_exclude(rel, exclude):
            continue
        filtered.add(rel)
    return frozenset(sorted(filtered))


def path_hit_ignore(path: str, ignore: Sequence[str]) -> bool:
    norm = path.replace("\\", "/").lstrip("./")
    for entry in ignore:
        ent = entry.replace("\\", "/").lstrip("./")
        if norm == ent or norm.startswith(ent + "/"):
            return True
    return False


def _config_content_sha256(root: Path, rel: str) -> str:
    try:
        return hashlib.sha256((root / rel).read_bytes()).hexdigest()
    except OSError:
        return "MISSING"


def manifest_fingerprint(manifest: IQCManifest, *, root: Path = REPO_ROOT) -> str:
    """Стабильный digest manifest + configured/verified pyright configs для supersession."""
    verified_cfg_sha = _config_content_sha256(root, manifest.verified_config_rel)
    configured_cfg_sha = _config_content_sha256(root, manifest.configured_config_rel)
    payload = {
        "policy_id": manifest.policy_id,
        "revision": manifest.revision,
        "activation": manifest.activation.value,
        "configured": manifest.configured_config_rel,
        "verified": manifest.verified_config_rel,
        "configured_config_sha256": configured_cfg_sha,
        "verified_config_sha256": verified_cfg_sha,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def assert_monotonic_verified_expansion(
    previous_include: Sequence[str],
    next_include: Sequence[str],
) -> None:
    """Запрещает сужение verified include."""
    prev = frozenset(previous_include)
    nxt = frozenset(next_include)
    removed = sorted(prev - nxt)
    if removed:
        raise IQCPolicyError(
            "MONOTONIC_VIOLATION",
            f"verified include shrink forbidden: {removed[:5]}",
        )


def enforce_mode_unchanged(previous_cfg: Mapping[str, Any], next_cfg: Mapping[str, Any]) -> None:
    """Запрещает ослабление typeCheckingMode / reportUnusedCallResult."""
    for key in ("typeCheckingMode", "reportUnusedCallResult"):
        if previous_cfg.get(key) != next_cfg.get(key):
            raise IQCPolicyError(
                "MONOTONIC_VIOLATION",
                f"forbidden change to {key}",
            )
