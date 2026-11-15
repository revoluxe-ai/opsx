# Fixture 01 — RED_PROVEN slice selected deterministically

## Given

Same APPLY DESIGN graph as apply-design fixture 01.

Receipts:

- `apply-red/SLICE-001/RED.json` status `RED_PROVEN`
- no `apply-implement/**` IMPLEMENTATION_WRITTEN
- no `apply-green/**`

## Expected

```text
SELECTED: SLICE-001
OTHER_ELIGIBLE: SLICE-002
NOT_SELECTED: SLICE-003
```

SLICE-003 waits on GREEN even if it later has RED. One slice per invoke.
