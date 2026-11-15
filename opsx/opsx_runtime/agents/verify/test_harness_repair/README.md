# test_harness_repair

Агент BUILD-оси: восстанавливает **collection/syntax** в `tests/**`, не product logic.

Отличие от `test_repair`: behavioral assertions и `WF_PYTEST_FAILURE`.

Вызывается из `workflows/verify/pytest_execute` collect-loop (primary). Ограниченный
dispatch fallback (`repair_route=test_harness`) — только если scope не в
`pytest_execute_finished_scopes`.

Граница с `coverage_activation`: strip/skip harness-only cohort members — не подменяет
collect repair здесь.
