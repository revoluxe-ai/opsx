# Fixture — S3.5 splits dirty paths across two work items

## Context

```text
change=auth-economy-multi-write
mission=ORCH-bug-fix-20260827T000000Z
CURRENT_STAGE: S3.5_WORKTREE_OWNERSHIP_RECONCILE
```

## Work items

```text
WI-1: store-layer rollback + reset-character message
  LOC_LINK: src/store/index.js
  MEMBER: CLR-store-rollback-001

WI-2: BankView timer gate
  LOC_LINK: src/components/views/BankView.vue
  MEMBER: CLR-bank-timer-002
```

## Dirty worktree (pre-existing partial repair)

```text
src/store/index.js          (partial WI-1 repair)
src/components/views/BankView.vue  (partial WI-2 repair)
```

## Expected WORKTREE_DELTA_MAP

```text
src/store/index.js → WI-1 / CLR-store-rollback-001
src/components/views/BankView.vue → WI-2 / CLR-bank-timer-002
CROSS_ITEM_ABSORPTION: FORBIDDEN
```

## S4 dispatch WI-2 envelope

```text
WORK_ITEM_SCOPE_PATHS: [src/components/views/BankView.vue]
PREEXISTING_PARTIAL_REPAIR_PATHS: [src/components/views/BankView.vue]
FORBIDDEN_PATHS: [src/store/index.js]
```

## Forbidden

```text
Commentary attributing BankView timer repair to WI-1
Dispatch WI-2 with store/index.js in scope
```
