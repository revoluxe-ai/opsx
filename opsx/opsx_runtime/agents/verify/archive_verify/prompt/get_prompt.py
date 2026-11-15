"""Промпт PHASE_ARCHIVE — archive-audit change перед ``/opsx-archive``."""

from __future__ import annotations

import json
from typing import Any


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
    change_root_rel = f"openspec/changes/{change_id}"
    spec_paths_json = json.dumps(
        artifact_bundle.get("spec_paths") or [],
        ensure_ascii=False,
        indent=2,
    )

    return f"""Ты — агент PHASE_ARCHIVE (`/opsx-verify mode=archive`): read-only **rollup**
verify receipts + policy blockers перед ``/opsx-archive``. **Не** повторяй полный
impl-vs-plan audit, если ``verify_receipt_rollup`` / ``IMPL_OK`` receipt текущий.

Репозиторий (workspace): {repository_path}
Change id: {change_id}

## Вердикт

- ``GO`` — machine preflight без gap; PA OK/not applicable; IMPLEMENTATION_TRACK закрыт.
- ``NO_GO`` — любой ``receipt_gaps`` / ``machine gaps`` из preflight (primary).

**Запрещено** в ``verdict``: IMPL_OK, IMPL_GAPS, «green».

## Измерения (кратко)

1. **Completeness** — IMPLEMENTATION_TRACK tasks only; post-impl Owner bullets не чекбоксы.
2. **Receipts** — impl/scenario/gates evidence из preflight ``verify_receipt_rollup``.
3. **Coherence** — только если preflight сигнализирует drift с ``IMPL_OK`` receipt.

Machine preflight gaps **обязательны** в ``gap_details`` при ``NO_GO``.
Не запускай full-suite pytest как единственный archive gate — hop receipts first.

## Product Acceptance

Поле ``product_acceptance_status``: PASS | FAIL | BLOCKED | NOT_APPLICABLE | NOT_VERIFIED.
PA правила встроены в preflight JSON ниже; не открывай ``.cursor/**``.

## Scope read/grep/glob

- Используй только INPUT CONTEXT (planning bundle + preflight) — без repo-wide scan.
- Не читай ``apply-*``, ``verify/**``, ``opsx/receipts/**``.
- production paths — только из встроенного bundle и preflight evidence

## Правила

- Не редактируй файлы, не вызывай ``/opsx-archive``, не sync specs.
- ``warnings`` — soft (например DESTRUCTIVE_CACHE_DEV_BEFORE_ARCHIVE); не единственный NO_GO.
- ``next_command`` — всегда ``""``.
- ``local_hop_copy_block`` — workspace, change, mode=archive, marker, gaps; при GO допустим ``/opsx-archive <change>``.

Текстовые summary-поля — **на русском**.

INPUT CONTEXT — planning artifacts:
{artifacts_json}

DETERMINISTIC PREFLIGHT (machine, merge при расхождении):
{preflight_json}

Delta spec paths:
{spec_paths_json}
"""
