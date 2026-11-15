# Break-glass exception route (bounded-verify + compose)

## Contract

After Owner `/opsx-break-glass` with open `CONTINUE_WITH_EXCEPTION`:

1. `openspec/changes/<change>/apply-process-exception/PROCESS-GATE-DEFERRAL.json`
   (`schema: opsx.break_glass_process_gate_deferral`, `provenance: OWNER_OVERRIDE`).
2. Original gate FAIL truth is preserved inside `actual_gate_results` (no fake GREEN/PASS).
3. `bounded_verify_split_apply._require_native_freshness` may proceed when
   `opsx-break-glass-process-gate-deferral.mjs split-apply-freshness` returns
   `exception_route: true`.
4. Ledger frontier: `mypy` FAIL does not block when `quality-deferral` reports
   `deferred: true` under the same open incident (FAIL evidence remains).

## Backfill

Existing incidents do not require a new Owner call. Consumers invoke
`ensureProcessGateDeferralForChange` (CLI: `ensure-deferral`) idempotently from
journals when identity/revision match.

## Forbidden

- Minting ordinary `GREEN_PROVEN`, `VERIFY_PASS`, or product PASS.
- Marking stale GREEN as `current: true` in native freshness classifiers.
