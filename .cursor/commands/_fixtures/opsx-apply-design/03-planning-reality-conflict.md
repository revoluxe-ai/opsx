# Fixture 03 — planning vs repository architectural contradiction

## Given

`change=fixture-conflict`

Frozen `design.md` states:

- introduce a **new** client-side ledger as the balance SoT
- do not use the existing API-backed progress store

Repository reality:

- `src/services/existingLedger.js` plus accepted decision
  `openspec/decisions/cloud-progress-sot-api/decision.md` already make the
  API the balance/progress SoT
- adding a second client ledger would change ownership and SoT

This is not "missing code the change is supposed to add"; it contradicts
approved architecture/ownership.

## Expected

```text
STATUS: BLOCKED
BLOCK_REASON: PLANNING_REALITY_CONFLICT
CHANGE: fixture-conflict
NEXT_STAGE: NONE
CHAT_HISTORY_REQUIRED: false
WRITE_BOUNDARY.product_code: false
WRITE_BOUNDARY.tests: false
```

Do **not** silently pick the existing ledger and claim READY.
Do **not** edit product code to "make the plan fit".
Do **not** rewrite design/spec/tasks.

Golden machine artifact: `03-planning-reality-conflict.APPLY-DESIGN.json`
