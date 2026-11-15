# Tier тестов

Маркер описывает ресурс. Стадия verify описывает, когда тест исполняется.

- Канонический маркер ресурса PostgreSQL: `requires_postgres`.
- Active-выражение ворот: `not requires_postgres`.
- Infra-выражение единственной исполняющей стадии: `requires_postgres`.
- Целевой контракт: `workflow_deferred` — производный признак infra-tier, его выставляет манифест, руками не писать.
- Пока продуктовые тесты не выровнены отдельной задачей, repair-приёмка `validate_mapped_workflow_deferred_marker` всё ещё требует явный маркер на postgres-пути. Meta-процесс файлы `tests/**` не правит.
- Hermetic-тест без PostgreSQL не помечается `requires_postgres`.
- На стадиях до `infra_tests` infra-тесты не запускаются: статус `DEFERRED`.
- Skip в infra-прогоне — это FAIL (`SKIP_AS_PASS`), не успех.
- Подключение к PostgreSQL при `STROY_TIER=active` — `TIER_VIOLATION`.
- Выражения берутся из `opsx/opsx_runtime/agents/common/test_tier_policy.py` (`ACTIVE_EXPR`, `INFRA_EXPR`). Второй список маркеров не заводить.
