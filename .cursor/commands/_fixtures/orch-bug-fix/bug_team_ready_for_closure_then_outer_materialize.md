# Fixture — Bug Team READY_FOR_CLOSURE → outer materialize

## Context

Bug Team completes repair episode and returns top-level:

```text
STATUS: READY_FOR_CLOSURE
WORK_ITEM_GREEN: YES
FANIN_READY: YES
LEARNING_DONE: YES
```

Durable finding row: `STATUS: green_confirmed` but not yet `closed_fixed`.

## Expected outer Orch (S5)

```text
BUG_TEAM_OUTCOME: BUG_TEAM_READY_FOR_CLOSURE
OUTER_ACTION: /opsx-materialize-bug-queue mode=update case=<CASE_ID> finding=<FINDING_ID> close=fixed
DUPLICATE_MATERIALIZE: NO (only because closed_fixed absent)
SSCA_REPAIR_TERMINAL: requires durable closed_fixed after materialize
BUG_BATCH_CLEAR: pending other mission members
```

## Forbidden

```text
assume Bug Team always auto-closed
skip materialize when CLOSED_FIXED absent
```
