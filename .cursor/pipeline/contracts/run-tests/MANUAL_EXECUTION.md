# /opsx-run-tests — manual execution contract (TARGET)

Contract id: `OPSX-WB-RUN-TESTS-01`

## Scope

This contract covers **manual** `mode=manual lane=pytest` invocations only.
Durable verification PASS for frozen S6 plans remains owned by
`bounded_verify_executor` via `/bounded-executor`.

## Helper surface

- Module: `.cursor/pipeline/run-tests/run_tests.py`
- Entry: `evaluate_manual_run(repo_root, arguments, runner=...)`
- Default runner: `poetry_pytest_runner` (`subprocess.run`, `shell=False`)

## Invariants

1. No test discovery, globs, or suite expansion.
2. No writes under `openspec/changes/**/bounded-verify/results/**`.
3. No ledger/plan/bindings mutations.
4. `destructive_local` selections fail closed before runner spawn.
5. `RUN_RESULT` is chat-only; `DURABLE_RECEIPT: NONE`.

## Bounded mode

Any `mode=bounded` request MUST return `BLOCKED` / `BINDING_GAP` without
executing tests on this target until a dedicated S6 pytest capability is
registered beside existing registry checks.

## Related

- `.cursor/commands/opsx-run-tests.md`
- `.cursor/static-check/contracts/destructive-selective-cache.md`
