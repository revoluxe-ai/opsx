# Prep green scope — agent playbook (v0.9.3 baseline normalizer)

Не вызывать `prep_green_scope.py`, `config_apply.py` (orchestration), `pyproject_pytest.py`, `tools/openspec/opsx_prep_green_scope.py`.

## STAGE1

1. `--deselect` в addopts → `BLOCKED` (`DESELECT_STILL_IN_ADDOPTS`).
2. `/opsx-static-check FULL` (once) → `PREP_GREEN_FULL_HANDOFF` → `disable_plan.extract_disable_plan`.
3. **No** `/opsx-test-env UP`, **no** Owner destructive policy prompt, **no** infra diagnosis.
4. **No** separate `pytest -m unit` inventory for DisablePlan.
5. Undeclared `pytest_destructive` in capabilities → `UNRESOLVED` for artifacts only; normalize destructive only if handoff includes the packet.
6. Executed pytest `FAIL` without failed node ids in handoff → `BLOCKED_INCOMPLETE_HANDOFF` (never whole-wave collect for that wave).
7. Write v2 artifacts (`artifacts.write_probe_and_plan`); reject STALE v3 JSON.
8. Show full DisablePlan; `AWAITING_GO` unless handoff truncated or check id missing.

## GO

1. `apply_disable.apply_disable_delta` → pyright / mypy / pytest skip in source.
2. `collect-only` per touched test file; skip hygiene as in command.
3. Rerun `/opsx-static-check FULL`; loop until FULL `PASS` or PAUSE (unsupported apply / handoff).
4. Do not hand off Owner to manual prep ↔ static-check loop or test-env.

## Helpers

| Module | Role |
|--------|------|
| `full_check.py` | FULL handoff → structured checks |
| `disable_plan.py` | DisablePlan + probe gate |
| `apply_disable.py` | Materialize delta |
| `converge.py` | Convergence verdict |
| `artifacts.py` | v2 probe/plan + STALE detection |
| `scenario_gate.py` | DISABLED_PROOF_SKIP oracle (audit parity) |
| `required_static_check_gate.py` | REQUIRED_STATIC_CHECK_DISABLED (audit parity) |

See `DISABLE_PLAN.md`, `ARTIFACT_SCHEMA.md`.
