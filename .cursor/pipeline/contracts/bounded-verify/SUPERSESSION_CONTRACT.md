# Bounded-verify plan supersession contract (v1)

## Invariants

1. **Immutable prior FROZEN plan** — archived under `bounded-verify/archive/<old_plan_id>/plan.json` (bytes unchanged).
2. **New identity** — successor publishes new `plan_id` + `plan_fingerprint` + v2 `implementation_snapshot`.
3. **Stale evidence** — results bound to old `plan_fingerprint` / `plan_id` MUST NOT drain into successor ledger (`RESULT_STALE_PLAN`, `LEDGER_STALE_PLAN_IDENTITY`).
4. **Atomic current pointer** — `bounded-verify/CURRENT_PLAN.json` records active `plan_id` + `supersedes_plan_id`.
5. **No silent carryover** — PASS artifacts from archived plan are not promoted without verified equivalence (new execution required).
6. **Recovery** — interruption leaves archive copy + current carrier; operator re-runs `publish_successor_plan` only after tree is intentional baseline.

## API

- Writer: `bounded_verify_supersession.publish_successor_plan(change_id, required_checks)`.
- Does **not** rewrite archived bytes or mutate pilot FROZEN plans in place.

## Split-APPLY

- v2 snapshot includes `role_digests.apply_green` from canonical `apply-green.json` receipt when present.
- Checkbox-only `tasks.md` edits alone do not satisfy split-APPLY completion.
