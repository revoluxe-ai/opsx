# Fixture 07 — unmet prior GREEN blocks downstream slice

## Given

Same APPLY DESIGN graph: SLICE-003 depends on SLICE-001 and SLICE-002.

Receipts:

- `apply-red/SLICE-001/RED.json` status `RED_PROVEN`
- `apply-red/SLICE-002/RED.json` status `RED_PROVEN`
- no `apply-green/**` GREEN_PROVEN

## Expected

```text
STATUS: BLOCKED
BLOCK_REASON: BLOCKED_DEPENDENCY_NOT_READY
SLICE: SLICE-003
UNMET: SLICE-001, SLICE-002
NEXT_STAGE: NONE
```

Do not treat missing upstream implementation as RED proof for SLICE-003.
