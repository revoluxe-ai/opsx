# Bounded Verify — Authority Split (SLICE-001 / R-V1-005A)

## Principle

One semantic fact — one authority. Commands are **interfaces only**; Python modules
enforce write boundaries.

## Plan (WHAT)

| Fact | Carrier | Write authority | Readers |
|------|---------|-----------------|---------|
| `planned_check_set` | `plan.json#required_checks[].check_id` | `bounded_verify_plan` (`draft_plan`, `freeze_plan`) | ledger, executor (later slices) |
| `frozen_plan_identity` | `plan.json#plan_fingerprint` + `plan_status=FROZEN` | `bounded_verify_plan.freeze_plan` | ledger bind, result bind (later) |

Runtime carrier: `openspec/changes/<change_id>/bounded-verify/plan.json`

## HOW (unchanged TARGET authority)

| Fact | Carrier | Write authority |
|------|---------|-----------------|
| `execution_capability_config` | `.cursor/static-check/registry.yaml` | **UNCHANGED** — read-only at freeze validation |

Freeze validates each `required_checks[].check_id` exists in registry; it does
**not** copy runner config into the plan.

## Command surface

SOURCE command names. TARGET Python modules sit behind them.

| Command | Role | Invokes | Must NOT |
|---------|------|---------|----------|
| `/opsx-plan-verification` | INTERFACE_ONLY | `bounded_verify_plan.freeze_plan` | write `plan.json` directly; execute checks; emit GO/NO_GO |
| `/opsx-next-check` | INTERFACE_ONLY | `bounded_verify_ledger.schedule_write` | execute checks; mutate `plan.json`; emit GO/NO_GO |
| `/bounded-executor` | INTERFACE_ONLY | `bounded_verify_executor.run_current_check` | schedule ledger; mutate `plan.json` or `ledger.json`; run without `mode=bounded` |

## Sole writer invariant

All supported mutations of `plan.json` go through
`.cursor/pipeline/bounded-verify/bounded_verify_plan.py`:

- `draft_plan(change_id, required_checks)`
- `freeze_plan(change_id)`
- `read_plan(change_id)`
- `assert_plan_mutable(change_id)`

Post-freeze mutation attempts raise `PLAN_FROZEN_MUTATION_REFUSED`. There is
no `--force`, `REGENERATE`, or `overwrite-frozen` escape.

Semantically new plan requires new `plan_id` and new publication; frozen plan
on the same change path remains immutable evidence.

## Out of scope

- Aggregate verification acceptance (`verification_obligation.py` — unchanged)
- `/opsx-verify` archive GO / NO_GO
- `/opsx-static-check` FULL or FAST waves
