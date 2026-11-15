# Fixture — Bug Team BLOCKED → no close, no Evolution waiver

## Context

MUST_FIX member with `DEBT_WAIVER_FORBIDDEN: YES`. Bug Team returns:

```text
STATUS: BLOCKED
WORK_ITEM_GREEN: NO
OWNER_DECISION_REQUIRED: YES
```

Disposition receipt still requires current-Change repair.

## Expected outer Orch

```text
BUG_TEAM_OUTCOME: BUG_TEAM_BLOCKED
OUTER_CLOSE: FORBIDDEN
EVOLUTION_TAIL_WAIVER: FORBIDDEN
MUST_FIX_REPAIR_STATUS: blocked (not waived into Evolution)
BUG_BATCH_CLEAR: NO
MISSION_STATUS: PARTIAL_COMPLETE or BLOCKED_OWNER_DECISION
```

## Forbidden

```text
fabricate close=fixed
drop MUST_FIX because debt tail exists
enqueue Evolution instead of repair
```
