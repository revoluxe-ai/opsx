# Fixture 01 — first eligible slice is deterministic

## Given

`change=fixture-hydrate` with APPLY_DESIGN_READY from
`../opsx-apply-design/01-ready-valid-change.APPLY-DESIGN.json`.

No `apply-red/**` receipts. No `apply-green/**` receipts.

SLICE-001 and SLICE-002 have empty `dependencies_on_prior_slices`.
SLICE-003 depends on 001 and 002.

## Expected

```text
SELECTED: SLICE-001
OTHER_ELIGIBLE: SLICE-002
NOT_SELECTED: SLICE-003
SELECTION: first eligible in slice_order.nodes
```
