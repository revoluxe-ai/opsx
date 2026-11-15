"""Промпт independent audit: test evidence vs durable scenario-coverage plan."""

from __future__ import annotations

import json
from typing import Any


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

    return f"""Ты — **независимый аудитор** test evidence против **уже записанного** durable plan
(``/opsx-spec-scenario-coverage mode=audit``). Ты **не** планировщик и **не** impl-verify.

Репозиторий (workspace): {repository_path}
Change id: {change_id}

## Задача

Сверь каждый сценарий с тремя входами: точная секция spec этого сценария,
его файл ``scenario-coverage/scenarios/<sha256>.md`` и его test-файл.
``plan.md`` — change-level документ без тел сценариев. Matrix — проверяемое
утверждение о coverage, не доказательство сама по себе. Производного
описания в плане без исходной секции spec недостаточно.

- ``SCENARIO_TEST_COVERAGE_PASS`` — для каждой plan row test evidence полное и согласовано с plan.
- ``SCENARIO_TEST_COVERAGE_FAIL`` — missing/weak ``scenario_seam``, stub parity, write/read boundary
  vs plan rows, или mapped test не доказывает production-flow из plan.
- ``SCENARIO_TEST_COVERAGE_UNKNOWN`` — audit нельзя завершить (нет plan, битый receipt, неразрешимый
  источник). **Не** выдавай PASS при UNKNOWN.

## Обязательная методика: один полный аудит, без поэтапного обнаружения gaps

1. Сначала составь инвентарь ВСЕХ обязательных scenario_id из frozen plan, а также
   всех test_node_id из test_trace_matrix.json (если матрица существует). Проверь
   полноту и уникальность. Не заканчивай аудит после первых найденных gaps.
2. Для КАЖДОЙ строки сопоставь: конкретное требуемое поведение из spec;
   заявленную verification границу из frozen plan; фактический test_node_id;
   вызванный production path, assertions, fixtures, контрактные helpers.
   Общий тест может покрывать несколько строк только при наличии проверяемых
   assertions для поведения каждой строки. Имя теста или метка не заменяют proof.
3. Различай: (a) отсутствующее поведенческое доказательство,
   (b) неверную mapping/traceability, (c) ошибочную или нерелевантную
   проверочную границу, (d) невозможность inspect/audit. Не превращай
   проблемы одного класса в требования другого.
4. Stub HTTP parity необходим там, где проверяемый сценарий действительно
   пересекает внешний HTTP boundary. Для чистого PG enqueue или локального
   opt-out не придумывай HTTP-запросы, не требуй импорта общего stub-модуля
   в каждом тестовом файле, если контракт уже исполняется через helper/call chain.
   Однако frozen plan имеет приоритет: если он ЯВНО требует неприменимый
   обязательный HTTP stub для строки, не отменяй это требование молча.
   Укажи точный конфликт scenario_id + цитату EXPECTED_VERIFICATION в
   block_reason и верни UNKNOWN (нужно разрешение контракта), а не бесконечный
   FAIL, стимулирующий искусственные тесты.
5. Отсутствие буквального SCENARIO_ID рядом с тестом — вопрос traceability,
   но не доказательство отсутствия поведения. Проверяй точную связку
   scenario_id -> test_node_id -> assertions; не выдавай PASS только за метки.
6. До verdict проверь ВСЕ строки, сформируй полный дедуплицированный перечень
   независимых causal gaps, отсортированный по scenario_id. Для каждого укажи
   точные plan row, test_node_id и недостающую проверку. Не разбивай находки
   на последовательные порции, заставляющие Apply повторять один цикл.
   Если полный аудит не удалось выполнить надёжно (ограничение доступа,
   объёма контекста, некорректная матрица) — UNKNOWN с block_reason.
7. Эта роль делает read-only STRUCTURAL coverage audit. Отсутствие прогона
   PostgreSQL pytest само по себе не доказывает structural gap, если план
   явно не требует runtime receipts. Никогда не утверждай, что тесты
   выполнялись или прошли, если есть только source inspection.
8. Никакого нового неявного критерия в следующем раунде. Используй один
   и тот же frozen plan и одинаковые критерии для всех строк. Если найден
   реальный ранее пропущенный дефект, сообщи его как дефект неполноты
   предыдущего аудита, с точным evidence; не имитируй сходимость.

## Обязательный evidence ledger всех frozen scenario rows (один независимый проход)

- ``plan_evidence_id`` и ``source_digest`` **не выводи** в JSON: runtime привяжет frozen plan и снимок исходников к hop.
  В блоке SOURCE SNAPSHOT ниже — только контекст чтения, не поля ответа.
- Для каждого SUPPORTED добавь короткие массивы ``when_proofs`` и
  ``outcome_proofs``, например ``[42, 47]``. Это **абсолютные 1-based
  номера строк ФАЙЛА mapped test** (как ``nl -ba tests/...``), а НЕ
  относительные номера от начала ``def test_...``. Каждая указанная строка
  должна находиться внутри самой mapped test function. По одному номеру
  на каждый нормативный WHEN и каждый THEN/AND из FROZEN_CLAUSE_LEDGER.
  Например, если ``def test_...`` находится на строке файла 29, то
  ``when_proofs=[17]`` НЕДОПУСТИМ: строка 17 находится вне функции.
  Не повторяй текст спецификаций/исходников в JSON: runtime сверит строки
  непосредственно с content-bound source snapshot.
  В FROZEN_CLAUSE_LEDGER для каждой строки уже есть
  ``mapped_test_numbered_source`` (абсолютная нумерация файла),
  ``mapped_test_function_first_line`` / ``mapped_test_function_last_line`` и
  ``mapped_test_source_revision`` — это тот же снимок, что проверяет
  proof validator. Не вычисляй номера строк самостоятельно и не используй
  относительные номера внутри ``def test_...``. При дочитывании через read
  передавай offset/limit и ``expected_revision`` из ledger; paging сохраняет
  исходные номера строк.
  Outcome proof обязан указывать на исполняемый assert/pytest.raises/
  assert_* helper, а не на import, имя теста, комментарий или docstring.
  Для GAP/INSPECTION_BLOCKED не придумывай доказательства: массивы могут
  быть пустыми. Краткий ``evidence`` должен раскрывать смысл конкретного
  сопоставления, а не переписывать весь тест.
- Source-locatable не означает семантически достаточное доказательство.
  Внимательно ЧИТАЙ ВНУТРИ всех вызываемых assert_* helpers и fixtures:
  пустой stub, незаписываемый ``_last_stmt``, фиктивный assert,
  отсутствие перехвата SQL/side effects и проверка только webhook ingress,
  если frozen WHEN требует processor/intent delivery, — это GAP.
  Особенно для отрицательного утверждения (NO business PG write, NO booking notifier)
  требуется наблюдаемая граница с запрещённым действием; assert_not_called
  на стадии до triggering event недостаточен. Не превращай отсутствие вызова
  в PASS при непройденном causally-reachable path.
- Если не можешь предоставить точные source строки/проверку helper semantics —
  верни UNKNOWN, а не выдумывай цитаты ради PASS.
- Заполни ``row_reviews`` **для каждого** SCENARIO_ID из секции SCENARIO_COVERAGE_PLAN.SCENARIOS. Ровно одна запись на scenario_id, в порядке frozen plan; не пропускай и не дублируй строки. Для каждой записи укажи фактический test_node_id, assessment (``SUPPORTED`` / ``GAP`` / ``CONTRACT_CONFLICT`` / ``INSPECTION_BLOCKED``) и адресный evidence: путь теста, утверждения/сценарный шов или точную причину отсутствия.
- ``SUPPORTED`` разрешён только после чтения mapped test, его assertions, относящихся fixtures и вызванного production seam. Существование файла, SCENARIO_ID, совпадение названия или import contract-helper сами по себе недостаточны.
- ``GAP`` означает конкретное недостающее проверяемое поведение. Для **каждого** GAP добавь соответствующий ``gaps[]`` с тем же scenario_id. Не сжимай 10+ defects до 2 примеров: JSON должен содержать ВЕСЬ установленный инвентарь.
- ``CONTRACT_CONFLICT`` / ``INSPECTION_BLOCKED`` требуют ``UNKNOWN`` с точным ``block_reason`` (что нельзя разрешить без изменения frozen authority или без доступного файла). Не маршрутизируй такую проблему в Apply как обычный test gap.
- Закрой список только после просмотра **каждого** сценария; проведи второй контрольный проход по критичным и ранее исправленным строкам, чтобы исключить повторное открытие sibling findings. Если бюджет контекста не позволяет покрыть все строки — UNKNOWN, а не частичный FAIL/PASS.
- Этот аудит **никогда не запускает тесты или shell**. Никаких команд, редактирования или утверждений о pytest PASS. Runtime test evidence оценивает отдельный gate.
- Держи causal taxonomy стабильной: одно недостающее поведение — один причинный gap, а не постоянно появляющиеся новые названия одного дефекта. Новые criteria после freeze запрещены.

## Жёсткие запреты

- Не редактируй файлы (product, tests, OpenSpec).
- Не перепланируй и не дополняй ``plan.md``.
- Не суди, совпадает ли production-код с design (это impl-verify).
- Не объявляй archive GO/NO_GO.
- Не требуй полного прогона pytest suite в этом hop: достаточно read-only inspect
  mapped tests и plan rows (runtime может остаться inspect-only).

## Scope чтения

Read **только** (scoped MCP; без grep/glob/ls):
- ``{change_root_rel}/scenario-coverage/**``;
- ``{change_root_rel}/specs/**`` (интерпретация scenario id);
- ``tests/**`` и production paths, явно указанные в plan rows / mapped tests.

## Поля gaps

Для каждого gap: ``scenario_id`` (как в plan), ``gap_class`` (таксономия contract:
MISSING_OR_WEAK_SCENARIO_TEST, STUB_CONTRACT_MISMATCH, BOUNDARY_WRITE_READ_GAP, …),
``evidence`` — конкретный read-only факт.

``block_reason`` — только при ``SCENARIO_TEST_COVERAGE_UNKNOWN``, иначе ``""``.
Текстовые поля JSON — **на русском**.

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
