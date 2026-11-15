# OPSX-ORCH-CORE-01 — Shared Orchestration Core

**Contract ID:** `OPSX-ORCH-CORE-01`  
**Consumes:** `OPSX-ORCH-ASYNC-TRANSPORT-01` for background worker hops  
**Scenario consumers:** `/opsx-orch-bug-fix`, future `/opsx-orch-verify`, `/opsx-orch-review`, …

This contract owns **HOW** orchestration works. It does **not** own scenario-specific
product semantics, disposition classes, or bug lifecycle writers.

## Separation of concerns

```text
CORE          mission identity, locks, dispatch envelope, ETA vs SSCA, terminalization
SCENARIO      DAG stages, routing matrix, batch-clear predicate
EXECUTOR      Task background (primary) or HOST_INLINE (logged fallback)
```

## Mission identity

Every orchestration run materializes a durable mission under:

```text
opsx/runs/orch-missions/<MISSION_ID>/
```

Schema: `schemas/mission.schema.json`.

Minimum persisted fields:

```text
MISSION_ID
SCENARIO_COMMAND
CHANGE_ID (when known)
MISSION_STATUS
CURRENT_STAGE
PLAN_IDS[]
WORK_ITEM_IDS[]
BUG_BATCH_CLEAR
STOP_UNCONFIRMED_WORKERS[]
PRODUCT_WRITER_LOCK_HOLDER
created_at / updated_at
```

`MISSION_ID` format: `ORCH-<scenario-slug>-<UTC>` (scenario command may refine).

## Work-item handles

Orch tracks opaque handles to upstream artifacts; it does **not** reinterpret their
semantics:

```text
PLAN_ID           → opsx/runs/bug-cases/family-plans/<PLAN_ID>.json
WORK_ITEM_ID      → work item within plan
DISPOSITION_ID    → opsx/runs/bug-cases/dispositions/<DISPOSITION_ID>.json
MEMBER_ID         → finding id within family / atomic item
CASE_ID           → when materialize/bug-team case scope applies
```

Duplicate dispatch prevention: the same `(mission_id, work_item_id, stage, command)`
tuple MUST NOT spawn a second active outer worker while a prior dispatch is
non-terminal or `STOP_UNCONFIRMED`.

## Resource locks

```text
MAX_PRODUCT_WRITERS=1
DEFAULT_LOCK_NAME=PRODUCT_WRITER
```

### Acquire

Before any outer worker that may edit product code (`/opsx-bug-team`, future product
writers):

```text
acquire PRODUCT_WRITER
→ record holder worker_id + dispatch_id in mission locks/product-writer.json
→ forbid second acquire until release
```

### Release (mandatory semantics)

```text
LOCK_RELEASE_ON_CONFIRMED_EXECUTION_TERMINAL=YES
LOCK_RELEASE_REQUIRES_GREEN=NO
```

Release **only** after **confirmed** execution terminality (see ETA below):

```text
EXECUTION_TERMINAL confirmed
→ reconcile reported/actual writes as far as runtime permits
→ release PRODUCT_WRITER lock
→ then evaluate semantic stage completion separately
```

If worker is blocked/failed semantically but execution-terminal, lock is still
released; the PP stage remains blocked without leaking the lock.

### STOP_UNCONFIRMED

When timeout elapsed but worker liveness cannot be positively confirmed:

```text
STOP_UNCONFIRMED=YES
→ PRODUCT_WRITER lock MUST NOT be reused
→ no retry
→ no next product writer
→ mission_status → BLOCKED_WORKER_STATE or BLOCKED_RESOURCE
→ Owner intervention / runtime recovery required
```

Hard invariant:

```text
TWO_POSSIBLY_LIVE_PRODUCT_WRITERS=FORBIDDEN
```

## Two authorities (never collapse)

### EXECUTION_TERMINAL_AUTHORITY (ETA)

Answers: *Has the outer worker actually stopped executing?*

```text
EXECUTION_TERMINAL (ETA) is reached ONLY with positive confirmation:

ETA_CONFIRM_SOURCES (any one sufficient):
  T1  Runtime Task/subagent terminal callback or result envelope
  T2  Confirmed WORKER_NOT_RUNNING / task handle reports not running
  T3  Durable runtime/transcript evidence of terminal completion when available

NEVER authoritative for ETA:
  UI Stopped / Waiting / Loading Chat / Preparing
  UI silence alone
  Missing receipt alone while worker may still be active
  Elapsed timeout alone
  Lost task handle alone
```

`TIMEOUT_REACHED` is **not** `EXECUTION_TERMINAL`. On timeout:

```text
TIMEOUT_REACHED
→ request stop/cancel when runtime supports it
→ harvest/check task state
→ require positive WORKER_NOT_RUNNING confirmation
→ only then: EXECUTION_TERMINAL → reconcile writes → release lock → allow bounded retry
→ if confirmation impossible: STOP_UNCONFIRMED (no lock reuse, no retry)
```

### SEMANTIC_STAGE_COMPLETION_AUTHORITY (SSCA)

Answers: *Did the PP stage achieve its durable semantic goal?*

Examples (scenario-specific details live in scenario command):

```text
disposition receipt persisted
BUG_TEAM_READY_FOR_CLOSURE / BUG_TEAM_ALREADY_DURABLY_CLOSED
durable CLOSED_FIXED
Evolution ownership receipt
deferred backlog durable row
false-positive terminal row
```

Rule:

```text
ETA != SSCA
execution-terminal worker may leave PP stage semantically incomplete
```

## AUTO_PERSIST delegation (routine lifecycle materialization)

Orch host may issue upstream `GO` / `GO_PERSIST_*` modes **without** Owner wake when
persistence is routine lifecycle materialization, not a semantic/authority decision.

```text
AUTO_PERSIST_DELEGATION:
  DELEGATED_GO_AUTHORITY=ORCH_HOST
  UPSTREAM_GO_SEMANTICS=GO_PERSIST_* only (not Owner approval)

  AUTO_PERSIST=REQUIRED when ALL:
    OWNER_DECISION_REQUIRED=none
    upstream semantic output valid (e.g. DISPOSITION_COMPLETE, PLAN_COMPLETE)
    no AUTHORITY_CONFLICT | NEEDS_ADJUDICATION | BLOCKED_NEEDS_EVIDENCE
    no unresolved MIXED_DISPOSITION routing split
    persistence scope is routine lifecycle materialization only

  OWNER_GO_REQUIRED=NO when AUTO_PERSIST=REQUIRED

  FORBIDDEN when AUTO_PERSIST=REQUIRED:
    NEXT_REAL_ACTION containing "Owner must send" / "awaiting GO"
    MISSION_STATUS: ACTIVE — awaiting GO
    pausing between READ_ONLY and persist on auto-delegable stages
```

Token disambiguation (mandatory):

```text
MISSION_GO          = Owner explicitly starts/resumes whole mission
DELEGATED_PERSIST   = Orch-issued upstream GO for plan/disposition/materialize
```

When `AUTO_PERSIST=REQUIRED`, Orch MUST execute `DELEGATED_PERSIST` in the same
autonomous loop iteration, satisfy SSCA (receipt on disk), then advance DAG —
never stop to request Owner `GO` for routine writes.

## Worker dispatch envelope

Schema: `schemas/worker-dispatch.schema.json`.

Every outer background dispatch MUST emit an Owner-visible status record containing
at least:

```text
DISPATCH_ID
TASK_ID (when available)
WORKER_ID
COMMAND
WORK_ITEM_ID
STATE=RUNNING
TRANSPORT=TASK_BACKGROUND|HOST_INLINE
WAITING_FOR=TERMINAL_RESULT
```

## Autonomous control loop

```text
AUTONOMOUS_POLL_HARVEST_LOOP=YES
OWNER_WAKE_REQUIRED=NO
SILENT_BLOCKING_WAIT=FORBIDDEN
```

After dispatch:

```text
parent orchestration control loop remains active
→ emit RUNNING state
→ poll/harvest actual task completion using ETA sources
→ continue DAG autonomously
→ no Owner message merely to resume polling
→ no duplicate Task to unstick UI
→ no wake prompt as automatic recovery
```

## Timeout / no-progress

```text
UI_SILENCE_IS_NON_AUTHORITATIVE=YES
NO_RETRY_FROM_UI_STOPPED=YES
NO_RETRY_FROM_MISSING_RECEIPT_WHILE_PLAUSIBLY_ACTIVE=YES
HARD_WALL_TIMEOUT=scenario-defined (logged)
MAX_OUTER_RETRIES_PER_WORK_ITEM=1
RETRY_REQUIRES_CONFIRMED_ETA=YES
```

Absence of UI events, transcript visibility, or durable artifacts for 15 minutes
MUST NOT alone prove a hung worker when runtime/task evidence suggests continued
execution.

## Retry gate

Bounded retry allowed only when **all** hold:

```text
prior worker EXECUTION_TERMINAL confirmed (not STOP_UNCONFIRMED)
PRODUCT_WRITER lock released after confirmed terminal
retry budget remains (≤1 default)
unchanged scope
```

## Cancellation

Owner cancel → stop dispatch loop → request worker stop when supported → harvest
terminal or STOP_UNCONFIRMED → never silently abandon lock holder.

## Terminalization hierarchy

```text
1. ETA confirmed (worker not running)
2. SSCA receipts for stage (scenario command)
3. Mission terminal predicate (scenario command, e.g. BUG_BATCH_CLEAR)
```

Publication/destructive-operation bans inherited from OPSX target override and
scenario command denylist (commit/push/PR/archive/deploy forbidden unless explicitly
authorized elsewhere).

## Owner-facing aggregate

Scenarios emit compact aggregate using schema
`schemas/owner-aggregate-output.schema.json`. Detailed receipts live under
`opsx/runs/orch-missions/<MISSION_ID>/receipts/`.

## Restart / recovery

On interrupted parent chat:

1. Read latest `mission.json` + worker dispatch records + lock file.
2. Resume from last non-terminal stage without duplicate active workers.
3. If `STOP_UNCONFIRMED` present → remain blocked; do not acquire PRODUCT_WRITER.
4. Re-harvest any RUNNING worker before new dispatch.

## Policy bans (core-level)

```text
SCENARIO_DAG_IN_CORE=FORBIDDEN
DISPOSITION_TRUTH_IN_CORE=FORBIDDEN
BUG_LIFECYCLE_WRITER_IN_CORE=FORBIDDEN
BUG_TEAM_INTERNAL_WAVES_IN_CORE=FORBIDDEN
GLOBAL_DEBT_BOARD_GATE_IN_CORE=FORBIDDEN
```

## Related files

- Async transport: `ASYNC_TRANSPORT.md`
- Bug-fix scenario: `.cursor/commands/opsx-orch-bug-fix.md`
- Schemas: `schemas/*.schema.json`
