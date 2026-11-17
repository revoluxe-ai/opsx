"""Промпт PHASE_IMPL — сравнение плана change с реализацией."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents.implementation.hop_read_scope import declared_product_paths_from_apply_design
from agents.verify.impl_verify.preflight import derive_referenced_repo_paths
from agents.verify.prompt.verify_prompt_fragments import verify_impl_hop_authority


def get_prompt(
    *,
    repository_path: str,
    change_id: str,
    artifact_bundle: dict[str, Any],
) -> str:
    """Собрать SDK prompt для ``ImplVerifyAgent``."""
    repo_root = Path(repository_path).resolve()
    referenced_paths = derive_referenced_repo_paths(
        change_id,
        artifact_bundle,
        repo_root=repo_root,
    )
    declared_product = declared_product_paths_from_apply_design(
        change_id,
        repo_root=repo_root,
    )
    work_paths = sorted(set(referenced_paths) | set(declared_product))
    artifacts_json = json.dumps(
        artifact_bundle,
        ensure_ascii=False,
        indent=2,
    )
    work_paths_json = json.dumps(work_paths, ensure_ascii=False, indent=2)
    change_root_rel = f"openspec/changes/{change_id}"
    spec_paths_json = json.dumps(
        artifact_bundle.get("spec_paths") or [],
        ensure_ascii=False,
        indent=2,
    )
    authority = verify_impl_hop_authority()

    return f"""{authority}

Репозиторий (workspace): {repository_path}
Change id: {change_id}

Конкретное дерево openspec в scope: ``{change_root_rel}/``.

Delta specs (repo-relative):
{spec_paths_json}

Явные production/decision paths (DERIVED_REFERENCED_PATHS + APPLY-DESIGN surfaces):
{work_paths_json}

INPUT CONTEXT — planning artifacts:
{artifacts_json}
"""