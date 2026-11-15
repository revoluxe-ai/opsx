# Fixture — Bug Team already closed_fixed → no duplicate materialize

## Context

Bug Team returns `READY_FOR_CLOSURE` or success, but durable case map already has:

```text
findings/<FINDING_ID>.md STATUS: closed_fixed
EPISODE_TERMINAL: FIXED
```

## Expected outer Orch (S5)

```text
BUG_TEAM_OUTCOME: BUG_TEAM_ALREADY_DURABLY_CLOSED
OUTER_ACTION: none (no second close=fixed)
SSCA_REPAIR_TERMINAL: YES for member
MATERIALIZE_INVOCATION_COUNT: 0
```

## Forbidden

```text
duplicate /opsx-materialize-bug-queue close=fixed
```
