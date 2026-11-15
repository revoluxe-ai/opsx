# Изоляция verify-логов от cwd агента (шаг 6 плана read-scope)

## Текущее состояние

- В workspace лежит **829** файлов `*.live.ndjson` (~194 МБ), преимущественно в `openspec/changes/*/verify/`.
- `.gitignore` не маскирует `*.live.log` / `*.live.ndjson` под `openspec/`.
- `grep` с `path=""` или корнем репо может возвращать эти артефакты в контекст агента (не ломает hop после deny-first, но раскрывает run-трейс).

## Целевая схема (внедрено)

1. **Один лог на CLI-запуск:** `opsx/logs/<change_id>/<session>.log` (все hop'ы и rows в том же файле).
2. **Receipts:** `opsx/receipts/<change_id>/` (`IMPL-VERIFY-RECEIPT.json`, `scenario-rows/`, `check-receipts/`, findings).
3. **openspec/changes/<id>/`** — только planning; `verify/` не пишется.
4. **Миграция legacy:** `opsx/opsx_runtime/scripts/migrate_openspec_runtime_artifacts.py` (`--dry-run`).
5. **`.gitignore`:** `opsx/logs/**`, `opsx/receipts/**`, `openspec/changes/**/verify/**`.
6. **Read-scope deny:** `opsx/logs/**`, `opsx/receipts/**`.
