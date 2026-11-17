"""Промпт independent audit: test evidence vs durable scenario-coverage plan."""

from __future__ import annotations

import json
from typing import Any

from agents.verify.prompt.verify_prompt_fragments import (
    verify_scenario_coverage_audit_authority,
)


def get_prompt(
    *,
    repository_path: str,
    change_id: str,
    plan_bundle: dict[str, Any],
    source_snapshot: dict[str, Any] | None = None,
    evidence_ledger: list[dict[str, Any]] | None = None,
) -> str:
    """Собрать prompt LLM-аудита для ``ScenarioCoverageVerifyAgent``."""
    bundle_json = json.dumps(plan_bundle, ensure_ascii=False, indent=2)
    snapshot_json = json.dumps(source_snapshot or {}, ensure_ascii=False)
    ledger_json = json.dumps(evidence_ledger or [], ensure_ascii=False, indent=2)
    change_root_rel = f"openspec/changes/{change_id}"
    authority = verify_scenario_coverage_audit_authority()

    return f"""{authority}

Репозиторий (workspace): {repository_path}
Change id: {change_id}

## Scope чтения (конкретные пути)

Read **только** (scoped MCP; без grep/glob/ls):
- ``{change_root_rel}/scenario-coverage/**``;
- ``{change_root_rel}/specs/**`` (интерпретация scenario id);
- ``tests/**`` и production paths, явно указанные в plan rows / mapped tests.

SOURCE SNAPSHOT (content-bound, read-only):
{snapshot_json}

FROZEN_CLAUSE_LEDGER (exact normative requirements for each scenario):
{ledger_json}

INPUT CONTEXT — durable plan bundle:
{bundle_json}
"""



def get_delta_prompt(
    *,
    repository_path: str,
    change_id: str,
    plan_evidence_id: str,
    source_snapshot: dict[str, Any],
    evidence_ledger: list[dict[str, Any]],
) -> str:
    """Independent fresh audit of only impact-selected frozen scenario rows."""
    ids = [item["scenario_id"] for item in evidence_ledger]
    clause_counts = "\n".join(
        f"- {item['scenario_id']}: WHEN={len(item['when_clauses'])}; THEN/AND={len(item['outcome_clauses'])}"
        for item in evidence_ledger
    )
    return f"""Ты — независимый read-only аудитор scenario test coverage.

Workspace: {repository_path}
Change: {change_id}

Входной frozen plan authority: {plan_evidence_id}
Scope: ТОЛЬКО следующие {len(ids)} scenario_id:
{json.dumps(ids, ensure_ascii=False, indent=2)}

ОБЯЗАТЕЛЬНЫЕ РАЗМЕРЫ МАССИВОВ ДЛЯ SUPPORTED:
{clause_counts}
Пример: WHEN=1; THEN/AND=2 означает when_proofs=[одна строка],
outcome_proofs=[строка THEN, строка AND]. Не добавляй лишние номера.

Предыдущие SUPPORTED результаты остальных сценариев сохранены и отдельно
проверяются детерминированным агрегатором. Их не перепроверяй, не перечисляй
в row_reviews и не пытайся сам установить итог всей проверки.

Проверь ВСЕ перечисленные scenario_id по тем же критериям, что и полный
scenario-coverage audit. Контекст каждой строки: точная секция spec
(``spec_section`` в ledger), файл плана этого сценария и его test-файл.
Для каждого:
- сопоставь секцию spec, файл сценария, WHEN/THEN clauses, test_trace_matrix mapping,
  test_node_id, реальный тестовый код, assertions, fixtures, helper chain,
  production behavior и boundary;
- имя теста, метка и похожее утверждение не являются доказательством coverage;
- принимай SUPPORTED только при конкретных WHEN и THEN/AND source lines,
  заполни when_proofs и outcome_proofs абсолютными номерами строк mapped test файла;
  для нумерации используй ``mapped_test_numbered_source`` и bounds в ledger
  (тот же snapshot, что у proof validator); schema retry не заменяет ledger;
- Массивы ПОЗИЦИОННЫЕ: один номер строки на один пункт frozen WHEN/THEN/AND,
  в том же порядке. Их длина СТРОГО равна числу пунктов соответствующего типа.
  Для WHEN=1 верни ровно один номер, даже если для setup использовано 3 строки.
  Дополнительные строки поясняй в evidence, а не в массиве proofs;
- outcome_proofs — исполняемые assert/pytest.raises/assert_* внутри mapped test,
  when_proofs также внутри этой функции; не используй номера из spec.md;
- если по пункту нет доказательства, верни GAP/INSPECTION_BLOCKED вместо
  выдуманной строки или искусственного PASS;
- для каждого GAP верни отдельный gaps[] с точным scenario_id и evidence;
- не добавляй требования за пределами frozen plan;
- ранее пропущенные реальные проблемы не скрывай;
- CONTRACT_CONFLICT и INSPECTION_BLOCKED требуют UNKNOWN и block_reason.

Верни строго JSON по схеме ответа LLM-аудита (без plan_evidence_id и source_digest).
row_reviews должны содержать РОВНО {len(ids)} записей в порядке списка выше;
ни одной записи других сценариев. PASS в ЭТОМ ответе означает только
SUPPORTED для каждой строки текущего подмножества, НЕ общий PASS всех 56.
Общий PASS принимает только детерминированный aggregate gate после полного
сведения актуальных receipts. FAIL требует полного перечня GAP в подмножестве.
UNKNOWN требует точной причины в block_reason.

Frozen authority (machine-bound, не в JSON ответа): plan_evidence_id={plan_evidence_id}; source_digest={source_snapshot["source_digest"]}
Не заявляй, что pytest выполнен: это read-only structural audit.

Только read (scoped MCP) в:
- openspec/changes/{change_id}/scenario-coverage/**
- openspec/changes/{change_id}/specs/**
- tests/** и production paths, используемые указанными тестами.

SOURCE SNAPSHOT:
{json.dumps(source_snapshot, ensure_ascii=False)}

FROZEN CLAUSE LEDGER — только выбранные строки:
{json.dumps(evidence_ledger, ensure_ascii=False, indent=2)}
"""
