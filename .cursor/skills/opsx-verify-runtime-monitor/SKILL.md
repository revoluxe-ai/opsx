---
name: opsx-verify-runtime-monitor
description: >-
  Monitor or unblock run_opsx_main verify (e.g. visual, security pass).
  META-only: fix opsx/opsx_runtime so the pipeline works. Never patch src/ or
  tests/ from chat while verify runs. Use when the goal is verify_impl_security_pass,
  SECURITY_COMPLETE, or watching a live verify foreground run.
---

# OPSX verify runtime monitor (META only)

Подключать вместе с `opsx/opsx.mdc` §27.0–§27.0.4.

## Журнал супервизора (жёстко)

Журнал супервизора — только SQLite (`opsx/runs/supervisor/<run_id>/journal.sqlite`, WAL, короткая транзакция на событие). Писать журнал в ndjson/jsonl запрещено: файл разрастается и вешает прогон. Читать хвост/status — через SQLite (`SupervisorJournal.rows_since` / `list_entries`), не tail `journal.ndjson`.

## Роль чата

Инженерный чат **не** является `apply_red`, `apply_green`, `logic_bug_repair` или `security_repair`. Он **чинит OPSX**, чтобы назначенные hops могли сделать продуктовую работу сами.

## Жёсткий запрет (без явного Owner GO на продукт)

Пока живой foreground `run_opsx_main.py` **или** цель — подтверждённый security pass (`verify_impl_security_pass`):

| Запрещено | Почему |
|-----------|--------|
| Любые правки `src/**`, `tests/**` (продукт) | Обход repair subgraph и receipts |
| «Быстрый фикс» под project_bug / pytest / security finding | Доказательство должно идти через hop |
| Ослабить `cli_outcome.py`, security validators, receipts | Ложный SECURITY_COMPLETE |
| Второй verify на тот же change | §27.0 `opsx/opsx.mdc` |
| `tail`/`grep` по `opsx/logs/**` вместо живого терминала | §27.0 `opsx/opsx.mdc` |

**Owner GO на продукт** — только если пользователь **дословно** поручил правку `src/`/`tests/` **вне** pipeline или остановил verify и поручил product fix отдельно.

## Разрешённые зоны META

- `opsx/opsx_runtime/**` — agents, workflows/verify, gate_repair, read_scope, tool lists
- `opsx_tools/**`, `run_opsx_main.py` (только если поручено META)
- `opsx/contract_prompts/**`, `.cursor/rules/**`, тесты под `opsx/opsx_runtime/**`

Типичные META-фиксы при verify:

- `READ_SCOPE_VIOLATION` на **repair/classifier** hop → tool policy, restrict paths, `run_pytest` wiring (как для `project_bug_hunt`, `apply_red`/`apply_green`)
- repair без pytest → `run_pytest` tool / executor, не возврат произвольного shell
- bridge / `NetworkError` → диагностика transport; **не** product patch
- зацикливание gate без сужения → `GATE_REPAIR_NO_PROGRESS` / stop по §27.0.1

## Наблюдение

1. Одна команда из `opsx/opsx.mdc` §27.0.0; не мешать foreground-run.
2. Прогресс — из **stdout/stderr того терминала**, не из side-log.
3. Успех security — только **`verify_impl_security_pass(state)`**: `SECURITY_COMPLETE`, 0 confirmed findings, saved `check_receipt` (`fresh_audit` / `durable_pass`). Exit 0 и маркеры в логе **не** считать pass.

## Действия при блокере

```text
Симптом в логе терминала
  → repair hop упал на scope/tools?     → META в opsx/opsx_runtime
  → repair hop работает, product FAIL?  → ждать/мониторить pipeline, чат не патчит src/
  → verify мёртв (bridge/exception)?    → META transport; resume только по поручению Owner
```

## Чеклист перед любой правкой файла

- [ ] Это **META** (`opsx/` или rules), не `src/`/`tests/`?
- [ ] Foreground verify **не** жив **или** правка **не** продукт?
- [ ] Валидаторы security **не** ослабляются?

Если любой пункт «нет» — **не** редактировать; описать блокер и нужный **hop** или META-файл.
