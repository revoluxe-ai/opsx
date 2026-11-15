# Fixture — S2 delegated persist without Owner wake

## Context

```text
change=auth-economy-multi-write
mission=ORCH-bug-fix-20260827T000000Z
CURRENT_STAGE: S2a_DISPOSITION_READ_ONLY
WORK_ITEM_ID: WI-FPLAN-20260827T000000Z-1
```

Disposition READ_ONLY completed with valid semantic output.

## Preconditions

```text
OWNER_DECISION_REQUIRED: none
AUTO_PERSIST_DELEGATE: eligible
STATUS: DISPOSITION_COMPLETE
WORK_ITEM_DISPOSITION: MUST_FIX_CURRENT_CHANGE
AUTHORITY_CONFLICT: NO
NEEDS_ADJUDICATION: NO
BLOCKED_NEEDS_EVIDENCE: NO
```

## Expected Orch behavior

```text
AUTO_PERSIST: REQUIRED
OWNER_GO_REQUIRED: NO
DELEGATED_PERSIST: /opsx-bug-disposition change=… plan=… work_item=… GO
CURRENT_STAGE_AFTER: S2b_DISPOSITION_AUTO_PERSIST → S3_ROUTE_BY_DISPOSITION
SSCA: dispositions/DISP-<PLAN>-<WI>.json exists
OWNER_WAKE: NO
```

## Forbidden

```text
NEXT_REAL_ACTION: Owner must send GO
MISSION_STATUS: ACTIVE — awaiting GO
stopping between S2a and S2b
```
