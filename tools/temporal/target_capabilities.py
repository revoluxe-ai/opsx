"""Target capability reader for the OPSX control-plane Temporal runtime."""
from __future__ import annotations

from opsx.project_context import resource_path, project_root

import json
import os
from pathlib import Path

_REPO_ROOT = project_root()
_CAPS = resource_path(_REPO_ROOT, "opsx", "project", "capabilities.json")


def load_capabilities() -> dict:
    if not _CAPS.exists():
        return {}
    return json.loads(_CAPS.read_text(encoding="utf-8-sig"))


def _normalized_roots(key: str) -> tuple[str, ...]:
    env_key = f"OPSX_TEMPORAL_TEST_{key.upper()}"
    if os.environ.get(env_key):
        roots = [part for part in os.environ[env_key].split(";") if part.strip()]
    else:
        caps = load_capabilities()
        roots = caps.get("product", {}).get(key, []) or []
    out: list[str] = []
    for raw in roots:
        value = str(raw).replace("\\", "/").strip().lstrip("./")
        if not value:
            continue
        if not value.endswith("/"):
            value += "/"
        if value not in out:
            out.append(value)
    return tuple(out)


def product_source_roots() -> tuple[str, ...]:
    return _normalized_roots("source_roots")


def product_test_roots() -> tuple[str, ...]:
    return _normalized_roots("test_roots")


def spec_change_roots() -> tuple[str, ...]:
    roots = _normalized_roots("spec_change_roots")
    return roots or ("openspec/changes/",)


def product_location_prefixes() -> tuple[str, ...]:
    return product_source_roots() + product_test_roots() + spec_change_roots()


def security_review_prefixes() -> tuple[str, ...]:
    return product_source_roots() + product_test_roots()


def product_lane_rank_map() -> tuple[tuple[str, int], ...]:
    return tuple((prefix, idx) for idx, prefix in enumerate(product_location_prefixes()))


def quality_command(kind: str) -> str | None:
    caps = load_capabilities()
    value = caps.get("quality", {}).get(f"{kind}_command")
    return str(value).strip() if value else None


def require_product_scope() -> tuple[str, ...]:
    prefixes = product_location_prefixes()
    if not (product_source_roots() or product_test_roots()):
        raise RuntimeError(
            "TARGET_CAPABILITY_UNRESOLVED: product.source_roots/test_roots are empty"
        )
    return prefixes


def authorized_repair_write_prefixes() -> tuple[str, ...]:
    """Write-scope for repair PRE/POST gates and commit-all (source + test roots only).

    Не включает ``spec_change_roots`` и никогда не расширяется до ``*``.
    """
    roots = product_source_roots() + product_test_roots()
    if not roots:
        raise RuntimeError(
            "TARGET_CAPABILITY_UNRESOLVED: product.source_roots/test_roots are empty"
        )
    if "*" in roots:
        raise RuntimeError(
            "TARGET_CAPABILITY_UNRESOLVED: wildcard write scope is forbidden"
        )
    return roots
