# OPSX-ORCH-ASYNC-TRANSPORT-01 — Async Worker Transport

**Contract ID:** `OPSX-ORCH-ASYNC-TRANSPORT-01`  
**Consumed by:** `OPSX-ORCH-CORE-01`, `/opsx-orch-bug-fix`, future `/opsx-orch-*`

Defines asynchronous/background dispatch requirements for long-running outer workers.
This contract does **not** fix Cursor UI bugs; it makes OPSX resilient to false UI
state and reduces harmful parent blocking.

## Purpose

```text
ASYNC_FIRST_TASK_BACKGROUND=YES
long-running outer workers use Task tool with run_in_background when supported
parent regains control immediately after dispatch
parent runs autonomous poll/harvest loop (see CORE.md)
```

## Transport modes

| Mode | When | Notes |
|------|------|-------|
| `TASK_BACKGROUND` | Default for long hops (Bug Team episode) | Primary v1 transport |
| `HOST_INLINE` | Background unavailable | Explicit logged fallback only |

Fallback MUST log:

```text
TRANSPORT_FALLBACK: HOST_INLINE
REASON: <capability gap>
```

## Dispatch rules

```text
SILENT_BLOCKING_WAIT=FORBIDDEN
OUTER_TASKS_PER_BUG_TEAM_EPISODE=1
OUTER_WAVE_DUPLICATION=FORBIDDEN
DUPLICATE_DISPATCH_ON_UI_STOPPED=FORBIDDEN
OWNER_WAKE_REQUIRED=NO
UI_STOPPED_IS_TERMINAL=NO
```

After dispatch the parent MUST emit (chat + durable worker record):

```text
TASK_ID: <when available>
WORKER_ID:
COMMAND:
WORK_ITEM_ID:
STATE: RUNNING
TRANSPORT: BACKGROUND
WAITING_FOR: TERMINAL_RESULT
```

## UI vs runtime authority

```text
UI labels (Stopped / Waiting / Starting / Loading Chat) = informational only
terminal authority prefers:
  Task result / callback
  agent transcript when available
  runtime task-handle NOT RUNNING confirmation
```

Forbidden:

```text
redispatch/retry solely because UI says Stopped
duplicate Task creation to unstick UI
Owner wake message as automatic recovery
treat UI silence as worker death
```

## Sequential product writers

```text
MAX_PRODUCT_WRITERS=1
previous outer worker must reach confirmed EXECUTION_TERMINAL before next writer
never overlap old/new product writers on same working tree
```

For bug-fix scenario: sequential `/opsx-bug-team` episodes — one background Task
per complete Bug Team invocation per MUST_FIX work item.

## Timeout interaction

```text
TIMEOUT != EXECUTION_TERMINAL
elapsed timeout alone never releases PRODUCT_WRITER lock
```

On `TIMEOUT_REACHED`:

1. Request stop/cancel when platform supports it.
2. Poll task handle / harvest state.
3. Require positive `WORKER_NOT_RUNNING` before ETA.
4. If not confirmable → `STOP_UNCONFIRMED` (see CORE.md).

## Observability (experimental lane)

Scenarios SHOULD record for later comparison:

```text
transport_mode
dispatch_at / harvest_at
ui_stopped_observed (boolean, non-authoritative)
false_stopped_incidence
completion_latency_ms
duplicate_dispatch_incidence
owner_intervention_required
```

Store under `opsx/runs/orch-missions/<MISSION_ID>/receipts/transport-*.json`.

## Opaque worker boundary (bug-fix)

Outer transport dispatches **one** complete command per episode:

```text
/opsx-bug-team plan=… work_item=… disposition=… [member_ids=…]
```

Orch MUST NOT:

- spawn nested outer Tasks for Bug Team internal WAVE_1–WAVE_4
- dispatch Bug Team internal worker roles directly
- interpret internal fanout/learning as outer stages

Bug Team internal Task fanout remains **inside** `/opsx-bug-team` contract.

## Non-goals

```text
CLOUD_AGENTS_MANDATORY=NO
GENERIC_SANDBOX=NO
LARGE .cursor/agents/** hierarchy as part of v1
CLAIM_UI_BUG_FIXED=NO
```

## Related

- Core: `CORE.md`
- Scenario: `.cursor/commands/opsx-orch-bug-fix.md`
- Worker schema: `schemas/worker-dispatch.schema.json`
