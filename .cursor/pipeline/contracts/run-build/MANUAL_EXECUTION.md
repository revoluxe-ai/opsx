# /opsx-run-build — manual execution contract (TARGET)

Contract id: `OPSX-WB-RUN-BUILD-01`

## Scope

This contract covers **manual** `build_recipe=poetry_package` invocations with an
explicit `output_dir` under `.cursor/pipeline/run-build/artifacts/` only.
Durable verification PASS for frozen S6 plans remains owned by
`bounded_verify_executor` via `/bounded-executor`.

## Helper surface

- Module: `.cursor/pipeline/run-build/run_build.py`
- Entry: `evaluate_manual_build(repo_root, arguments, runner=...)`
- Default runner: `poetry_build_runner` (`subprocess.run`, `shell=False`)
- Resolved command: `poetry build --output <owned_dir> --no-interaction`

## Invariants

1. No implicit build when `build_recipe` or `output_dir` is missing.
2. No writes under `openspec/changes/**/bounded-verify/results/**`.
3. No ledger/plan/bindings mutations.
4. No default `dist/` at repo root — owned artifacts prefix only.
5. `RUN_RESULT` is chat-only; `DURABLE_RECEIPT: NONE`.
6. `npm_run_build` and vue-npm `frontend_build` are `UNAVAILABLE` on this target.

## Bounded mode

Any `mode=bounded` request MUST return `BLOCKED` / `BINDING_GAP` without
executing a build until an explicit S6 package-build capability is registered
beside existing frozen registry checks.

## Related

- `.cursor/commands/opsx-run-build.md`
- `opsx/project/quality-gates.md` (`poetry-python` adapter)
