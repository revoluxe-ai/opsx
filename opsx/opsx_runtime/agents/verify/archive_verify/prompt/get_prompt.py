"""Промпт PHASE_ARCHIVE — archive-audit change перед archive stage."""

from __future__ import annotations

import json
from typing import Any

from agents.verify.prompt.verify_prompt_fragments import verify_archive_hop_authority


def get_prompt(
    *,
    repository_path: str,
    change_id: str,
    artifact_bundle: dict[str, Any],
    preflight: dict[str, Any],
) -> str:
    """Собрать SDK prompt для ``ArchiveVerifyAgent``."""
    artifacts_json = json.dumps(artifact_bundle, ensure_ascii=False, indent=2)
    preflight_json = json.dumps(preflight, ensure_ascii=False, indent=2)
    spec_paths_json = json.dumps(
        artifact_bundle.get("spec_paths") or [],
        ensure_ascii=False,
        indent=2,
    )
    authority = verify_archive_hop_authority()

    return f"""{authority}

Репозиторий (workspace): {repository_path}
Change id: {change_id}

INPUT CONTEXT — planning artifacts:
{artifacts_json}

DETERMINISTIC PREFLIGHT (machine, merge при расхождении):
{preflight_json}

Delta spec paths:
{spec_paths_json}
"""