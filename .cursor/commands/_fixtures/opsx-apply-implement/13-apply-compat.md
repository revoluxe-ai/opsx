# Fixture 13 — existing `/opsx-apply` remains operational

## Given

`/opsx-apply-implement` is installed. Owner runs `/opsx-apply change=<id>` with
no IMPLEMENTATION receipts.

## Expected

```text
APPLY_REQUIRES_IMPLEMENTATION_WRITTEN: no
APPLY_RUNTIME_CLASS: ACTIVE
APPLY_STILL_IMPLEMENTS_TASKS: yes
```
