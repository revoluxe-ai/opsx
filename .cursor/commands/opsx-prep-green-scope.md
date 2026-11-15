---
component_id: opsx.commands.prep_green_scope
component_version: 0.9.3
ownership: TARGET_ONLY
runtime_class: ACTIVE
contract_id: OPSX-PREP-GREEN-SCOPE-V1
required_adapters: ["opsx-lite", "project-generic"]
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
argument-hint: "[GO]"
---

# /opsx-prep-green-scope

**Baseline normalizer:** подготовить репозиторий к честному **`/opsx-static-check FULL PASS`**
путём **явного** отключения всего, что в baseline probe **не PASS**. Не чинить product bugs.
Не ослаблять assertions ради change. Не `--deselect`, не global mypy ignore, не pytest hooks.

**Deterministic mechanism (process):** `.cursor/pipeline/prep-green-scope/`
(`disable_plan.py`, `apply_disable.py`, `converge.py`, `artifacts.py`).
**Authority:** `DISABLE_PLAN.md`, `ARTIFACT_SCHEMA.md`, `AGENT_PLAYBOOK.md`.

Скрипты `prep_green_scope.py`, `config_apply.py` (orchestration), `opsx_prep_green_scope.py`
— **DEPRECATED**; не вызывать для STAGE1/GO.

## Canonical rule

| FULL check terminal | Baseline action |
|---------------------|-----------------|
| `PASS` | keep enabled |
| anything else (`FAIL`, `BLOCKED`, `ERROR`, `NOT_EXECUTED`, `suite_not_executed`, missing runtime, collection failure attributable to tests, …) | **DisablePlan** → native suppression only |

**Forbidden in this command:** repair infrastructure; **`/opsx-test-env UP`**; diagnose root cause;
Owner prompts for destructive applicability; classify *why* baseline cannot execute.

**PAUSE only** when suppression cannot be applied safely and exactly (unsupported pytest
mutation shape, truncated handoff, missing handoff check packet).

## Preconditions

1. `pyproject.toml` `[tool.pytest.ini_options].addopts` без `--deselect`  
   → иначе `STATUS: BLOCKED`, `REASON: DESELECT_STILL_IN_ADDOPTS`.

Env / Docker / destructive policy **не** блокируют STAGE1 или GO. Pytest **executed
`FAIL`** без failed node ids в handoff → **`BLOCKED_INCOMPLETE_HANDOFF`** (не
whole-wave collect). Whole-wave pytest fallback только для `BLOCKED` /
`NOT_EXECUTED` / `ERROR` / `suite_not_executed` / zero executable tests.

## STAGE1 (`/opsx-prep-green-scope`)

1. Прогнать **`/opsx-static-check FULL`** один раз до Final (registry.yaml, все волны;
   красный unit **не** отменяет integration/destructive).
2. Consume **`PREP_GREEN_FULL_HANDOFF`** JSON only →
   `handoff_consume.load_full_from_handoff` → **`extract_disable_plan`**.
   **Запрещено** парсить human Final / CHECKS / PROBLEMS; missing/invalid handoff →
   **`BLOCKED_MISSING_FULL_HANDOFF`**. Truncated handoff → **`BLOCKED_INCOMPLETE_HANDOFF`**
   (no partial DisablePlan).
3. **`BLOCKED_INCOMPLETE_PROBE`** только если scheduled check **отсутствует** в handoff
   (не из-за `BLOCKED` / `NOT_EXECUTED` / env на волне).
4. Записать `opsx/prep-green-scope/probe-result.json` + `plan.json` (**schema v2**).
   Artifact v3 / `pytest_deselect_count` / global mypy → **`STALE`** (не управляет GO).
5. Показать Owner полный DisablePlan (что будет отключено).  
   `STATUS: AWAITING_GO` + `NEXT_ACTION: GO` (если gate не handoff-blocked). Product не менять.

**Destructive wave:** prep-green **не** выводит applicability из capabilities.
Обрабатывается только то, что есть в handoff FULL: `pytest-destructive` **PASS** → keep;
non-PASS → DisablePlan; packet отсутствует → wave не нормализуется.

## GO (`/opsx-prep-green-scope GO`)

Агент **владеет convergence** (Owner не чередует prep ↔ static-check):

```text
LOOP (safety cap — не success criterion):
  apply bounded disable delta (apply_disable.py)
  → collect-only hygiene на каждый изменённый test module
  → canonical /opsx-static-check FULL
  → extract remaining candidates from non-PASS
  → terminal FULL PASS | next delta | PAUSE (unsupported apply only)
```

| Non-PASS evidence | Native suppression |
|-------------------|-------------------|
| pyright | exact file → `ignore` in `pyrightconfig_strict.json` |
| mypy | exact module → `[[tool.mypy.overrides]]` + `ignore_errors = true` |
| pytest | `@pytest.mark.skip(reason="prep-green-scope: …")` on failing node |

**Stop PAUSE/FAIL:** unsupported pytest source shape; truncated/missing handoff;
same apply signature без прогресса после safety cap; **не** env/destructive policy.

**Terminal success (only):**

```text
STATUS: PASS
VERDICT: PREP_GREEN_SCOPE_READY
FULL_STATIC_CHECK: PASS
remaining_disable_candidates: 0
blocked_required_checks: 0
```

После terminal PASS: optional `/opsx-commit GO` (packaging only).

## Scenario audit (inverse gate — not this command)

| Condition | Row verdict |
|-----------|-------------|
| Required pytest id skipped by prep-green | `FAIL` / `DISABLED_PROOF_SKIP` |
| Change-required file in pyright `ignore` | `FAIL` / `REQUIRED_STATIC_CHECK_DISABLED` |
| Change-required module under mypy `ignore_errors` | `FAIL` / `REQUIRED_STATIC_CHECK_DISABLED` |

Implementation flow: снять suppression и сделать evidence green для change.
See `required_static_check_gate.py`, `SCENARIO_TEST_COVERAGE.md`.

## Acceptance

`poetry run pytest .cursor/pipeline/prep-green-scope/tests/ -q` — mandatory green
before declaring command repaired.

## Related

- `/opsx-static-check`, `/opsx-spec-scenario-coverage` (inverse gates)
