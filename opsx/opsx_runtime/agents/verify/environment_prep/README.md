# verify_environment_prep

Агент **первого прохода** gate-repair: пакетно чинит systemic pytest collection,
shared fixtures, hermetic workspaces и test env **до** per-finding dispatch.

Отличие от `test_harness_repair`:

- шире по scope (env contracts, несколько когорт за один hop);
- живёт в subgraph **после** `group_error`, не внутри `pytest_execute` loop;
- после hop — обязательный collect proof и опциональный re-run `quality_gates`.

Opt-out: `OPSX_SKIP_ENVIRONMENT_PREP=1`.  
Лимит: `MAX_ENVIRONMENT_PREP_ATTEMPTS` (=2) в `cohort_detect.py`.
