# Fixture 09 — stale or invalid APPLY DESIGN

## Given

`change=fixture-hydrate` but APPLY-DESIGN.json is missing, not READY, 
`next_stage` is not APPLY_SCAFFOLD, `planning_digest` does not match current
planning bytes, or `protected_context_identity` drifted. Original
`inspected_digest` is baseline provenance and does not stale DESIGN merely
because authorized pipeline writes changed product/test surfaces.

## Expected

```text
STATUS: BLOCKED
BLOCK_REASON: STALE_OR_INVALID_APPLY_DESIGN | MISSING_APPLY_DESIGN
NEXT_STAGE: NONE
NEXT_ACTION: not /opsx-apply-implement
```

Do not invent slices from chat history.
