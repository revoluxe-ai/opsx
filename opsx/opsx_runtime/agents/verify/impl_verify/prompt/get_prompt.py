"""Промпт PHASE_IMPL — сравнение плана change с реализацией."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents.implementation.hop_read_scope import declared_product_paths_from_apply_design
from agents.verify.impl_verify.preflight import derive_referenced_repo_paths


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

    return f"""Ты — агент PHASE_IMPL (`/opsx-verify mode=impl`): read-only сверка **реализации** change с plan/design/tasks/delta specs; **не** судишь тесты, pytest, coverage и ``tests/**``.

Репозиторий (workspace): {repository_path}
Change id: {change_id}

## Суть вердикта

Сравни **план change** (proposal, design, tasks, delta specs, owner-contract) с **фактической production-реализацией** по путям, которые naming-артефакты change.
- ``IMPL_OK`` — запланированное поведение, размещение и tasks согласованы с кодом в scope.
- ``IMPL_GAPS`` — в плане есть пункт, которого нет в коде, или код противоречит плану.

``gap_details`` — только **продуктовые** расхождения (что планировали vs что в коде). **Не** включай STALE_EVIDENCE, STALE_GREEN, HEAD drift, TASK-COMPLETION receipts, task-projection counters и прочую «бумажную» staleness; **не** включай отсутствие/слабость тестов (это hop ``scenario_coverage_verify``).

## Жёсткий scope (только этот change)

**Запрещён** поиск по корню репозитория и по дереву ``openspec/`` вне ``openspec/changes/{change_id}/``; единственное дерево openspec в scope — ``{change_root_rel}/``.

Tool ``read`` — только по явным repo-relative путям внутри:
- ``{change_root_rel}/**`` (planning-артефакты change);
- production/decisions paths, явно названные в planning-артефактах ниже (backticks, пути в tasks/design).

**Вне scope:** ``tests/**``, pytest, scenario test coverage, mapped test files — не читай и не оценивай; полнота test evidence — hop ``scenario_coverage_verify`` после ``IMPL_OK``.

Delta specs (repo-relative):
{spec_paths_json}

Явные production/decision paths (DERIVED_REFERENCED_PATHS + APPLY-DESIGN surfaces) — **сначала** читай их через ``read``, не угадывай имена файлов миграций:
{work_paths_json}

Запрещено: другие ``openspec/changes/<другой_id>/``, dogfood без связи с этим change.

## Правила

- Не редактируй файлы, не коммить, не объявляй archive GO/NO_GO.
- Маркеры для Owner: только IMPL_OK / IMPL_GAPS в поле ``verdict`` JSON-ответа (не печатай «green» / ``✅ IMPL_OK`` вместо финального JSON).
- Machine receipts в ``opsx/receipts/**`` недоступны модели; не пытайся их читать.
- Промежуточные assistant/thinking сообщения и tool results — не финальный ответ; **финальное** сообщение hop — **только** один JSON по схеме ниже, после завершения read-only проверки.
- Проверяй tasks ``[x]`` против доказательств в коде; поведение vs IN_SCOPE / delta specs change.
- Архитектурное размещение vs decisions, указанные в design change.
- EXTERNAL_CALL_COVERAGE / PERFORMANCE / static-check / test coverage — только заметки в текстовых полях при необходимости, **не** hard blocker для ``IMPL_OK`` и **не** предмет вердикта.
- ``next_command`` — **всегда пустая строка** ``""`` (маршрутизацию делает граф по ``verdict``).
- ``local_hop_copy_block`` — plain text: workspace path, change id, marker, gaps; **без** выдуманных slash-команд «следующего hop``.

Текстовые поля отчёта в JSON — **на русском**.

Поля JSON (смысл секций отчёта):
- behavior_summary → ## Поведение
- architecture_placement → ## Архитектурное размещение
- tasks_consistency → ## Согласованность tasks
- gap_details → список gap (пустой при IMPL_OK)

INPUT CONTEXT — planning artifacts:
{artifacts_json}
"""
