# Fixture 14 — existing `/opsx-apply` remains operational

## Given

`/opsx-apply-green` is installed. Owner runs `/opsx-apply change=<id>` with no
GREEN receipts.

## Expected

```text
APPLY_REQUIRES_GREEN_PROVEN: no
APPLY_RUNTIME_CLASS: ACTIVE
APPLY_STILL_IMPLEMENTS_TASKS: yes
```
