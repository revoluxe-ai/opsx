# Fixture — timeout without liveness confirmation

## Context

Outer Bug Team Task dispatch `DISPATCH-001`:
- `HARD_WALL_TIMEOUT` elapsed
- UI shows Stopped
- No Task terminal callback
- `WORKER_NOT_RUNNING` cannot be confirmed
- Runtime stop/cancel did not produce confirmation

## Expected outer Orch

```text
TIMEOUT_REACHED: YES
EXECUTION_TERMINAL: NO
ETA_FROM_TIMEOUT_ALONE: FORBIDDEN
STOP_UNCONFIRMED: YES
PRODUCT_WRITER_LOCK_RELEASE: NO
RETRY: FORBIDDEN
NEXT_PRODUCT_WRITER: FORBIDDEN
MISSION_STATUS: BLOCKED_WORKER_STATE
TWO_POSSIBLY_LIVE_PRODUCT_WRITERS: FORBIDDEN
```

## Forbidden

```text
release lock because timeout elapsed
spawn retry Task while liveness unknown
treat UI Stopped as terminal
```
