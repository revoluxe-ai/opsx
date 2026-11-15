---
description: "Compose a DRAFT bounded verification plan from registry waves (WHAT only)."
argument-hint: "change=<change-id> [profile=static_fast|static_full|bounded_minimal|full_required]"
---

# /opsx-compose-verification-plan

**INTERIM (Owner GO 2026-10-29):** post-implementation happy path — **`/opsx-static-check FULL`**
→ archive verify; compose spine **не обязателен** до смены policy.

**INTERFACE ONLY** — invokes `verification_plan_compose.compose_verification_plan`
→ `bounded_verify_plan.draft_plan`.

## Preconditions

- Change with `tasks.md` impl-complete signal (≥1 `[x]`).
- No FROZEN plan on change (`PLAN_FROZEN_MUTATION_REFUSED` otherwise).
- Split-APPLY: ordinarily requires CURRENT GREEN per slice; under open Owner break-glass, `PROCESS-GATE-DEFERRAL.json` + `OWNER_OVERRIDDEN` allows DRAFT while stale GREEN remains recorded (see `.cursor/pipeline/contracts/bounded-verify/BREAK_GLASS_EXCEPTION_ROUTE.md`).

## Profiles

| Profile | Meaning |
|---------|---------|
| `static_fast` | FAST registry waves — **static-only**; not full verification completion |
| `static_full` | FULL registry waves incl. destructive — **static-only** |
| `bounded_minimal` | Wave A only (pyright + mypy + targeted) |
| `full_required` | **FAIL_CLOSED** until S5 adapters exist (`PLAN_COVERAGE_INCOMPLETE`) |

## Does NOT

- Freeze plan (`/opsx-plan-verification`).
- Execute checks or write ledger.
- Write `verify/VERIFICATION-PLAN.json` (retired SOURCE carrier).

## Steps

1. Parse `change=<id>` and optional `profile=` (default `static_fast`).
2. From repo root:

```bash
poetry run python -c "import sys; from pathlib import Path; sys.path.insert(0, str(Path('.cursor/pipeline/bounded-verify'))); from verification_plan_compose import compose_verification_plan; r = compose_verification_plan('<change_id>', '<profile>'); print('DRAFT', r.draft.plan_id, 'checks', len(r.check_ids))"
```

3. Next: Owner validates DRAFT → `/opsx-plan-verification change=<id>` → `/tempo-verification change=<id> low low`.
