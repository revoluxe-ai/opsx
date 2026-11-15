# Fixture — RELEASE_CLEAR passthrough from disposition (MUST_FIX only)

## Context

```text
change=auth-economy-multi-write
plan=FPLAN-20260827T000000Z
WORK_ITEM_DISPOSITION: MUST_FIX_CURRENT_CHANGE
```

No GLOBAL_RELEASE_BLOCKER or SECURITY_TRIAGE_REQUIRED members in batch.

## Disposition receipt (authority)

```text
CHANGE_CLEAR: NO
RELEASE_CLEAR: NO
GLOBAL_BLOCKER_MEMBER_IDS: []
SECURITY_MEMBER_IDS: []
```

Per /opsx-bug-disposition: RELEASE_CLEAR requires CHANGE_CLEAR=YES first.

## Expected Orch OWNER_AGGREGATE

```text
FIX_NOW: [MUST_FIX_CURRENT_CHANGE items]
GLOBAL_BLOCKERS: []
SECURITY: []
UNRESOLVED: []
CHANGE_CLEAR: false
RELEASE_CLEAR: false
AGGREGATE_SOURCE: disposition receipts
AGGREGATE_RECOMPUTE: FORBIDDEN
```

## Note

`RELEASE_CLEAR=false` with only MUST_FIX_CURRENT_CHANGE is disposition-correct,
not an Orch aggregate bug.
