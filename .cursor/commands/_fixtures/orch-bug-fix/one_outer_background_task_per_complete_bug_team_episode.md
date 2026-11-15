# Fixture — one outer background Task per Bug Team episode

## Context

Two MUST_FIX work items in same mission: `WI-1`, `WI-2`.

## Expected outer Orch (S4)

```text
WI-1:
  OUTER_TASK_COUNT: 1
  COMMAND: /opsx-bug-team plan=… work_item=WI-1 disposition=…
  TRANSPORT: TASK_BACKGROUND
WI-2:
  OUTER_TASK_COUNT: 1
  starts only after WI-1 worker confirmed EXECUTION_TERMINAL + lock released
TOTAL_OUTER_TASKS: 2 (sequential, not nested waves)
```

## Forbidden

```text
multiple outer Tasks for same work item episode
outer Task per WAVE_1/2/3/4
parallel product writers
```
