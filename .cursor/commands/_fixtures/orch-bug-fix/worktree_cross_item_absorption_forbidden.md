# Fixture — partial repair must not cross work-item boundaries

## Context

```text
change=auth-economy-multi-write
mission=ORCH-bug-fix-20260827T000000Z
CURRENT_STAGE: S3.5_WORKTREE_OWNERSHIP_RECONCILE
dispatch_target: WI-2
```

WI-1 already has a valid partial repair in `src/store/index.js`.
WI-2 owns `src/components/views/BankView.vue` only.

## Preconditions

```text
WI-1 partial repair: src/store/index.js (in progress, not yet closed_fixed)
WI-2 scope: src/components/views/BankView.vue
dirty paths: both files present
```

## Expected

```text
REUSE_PARTIAL_REPAIR: allowed for WI-1 on src/store/index.js only
CROSS_ITEM_ABSORPTION: FORBIDDEN
WI-2 dispatch MUST NOT claim src/store/index.js
WI-2 tests: BankView timer repair only
```

## Forbidden

```text
WI-2 worker commits or credits store/index.js changes as WI-2 repair
Silent absorption of WI-1 delta into WI-2 commentary or closure
Duplicating WI-1 store repair during WI-2 episode
```
