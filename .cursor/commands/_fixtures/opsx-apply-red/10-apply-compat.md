# Fixture 10 — existing `/opsx-apply` remains operational

## Given

`/opsx-apply-red` is installed. Owner runs `/opsx-apply change=<id>` with no
RED receipts.

## Expected

```text
APPLY_REQUIRES_RED_PROVEN: no
APPLY_RUNTIME_CLASS: ACTIVE
APPLY_STILL_IMPLEMENTS_TASKS: yes
```
