# Fixture — S5b delegated materialize without Owner wake

## Context

```text
change=auth-economy-multi-write
mission=ORCH-bug-fix-20260827T000000Z
CURRENT_STAGE: S5a_REPAIR_CLOSURE_EVAL
WORK_ITEM_ID: WI-FPLAN-20260827T000000Z-1
```

Bug Team returned READY_FOR_CLOSURE; no durable closed_fixed yet.

## Preconditions

```text
OWNER_DECISION_REQUIRED: none
AUTO_PERSIST_DELEGATE: eligible
BUG_TEAM_READY_FOR_CLOSURE: YES
WORK_ITEM_GREEN: YES
FANIN_READY: YES
LEARNING_DONE: YES
DURABLE_CLOSED_FIXED: NO
```

## Expected Orch behavior

```text
AUTO_PERSIST: REQUIRED
OWNER_GO_REQUIRED: NO
DELEGATED_PERSIST: /opsx-materialize-bug-queue mode=update … close=fixed
CURRENT_STAGE_AFTER: S5b_MATERIALIZE_AUTO_PERSIST
SSCA: durable closed_fixed exists
OWNER_WAKE: NO
```

## Forbidden

```text
NEXT_REAL_ACTION: Owner must send GO for materialize
waiting for Owner approval on routine closure persist
```
