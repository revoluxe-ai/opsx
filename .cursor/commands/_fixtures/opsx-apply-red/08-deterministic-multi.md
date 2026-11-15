# Fixture 08 — multiple independent eligible slices

## Given

APPLY DESIGN with SLICE-001 and SLICE-002 both dependency-free and not RED_PROVEN.
`slice_order.nodes` lists SLICE-001 before SLICE-002.

## Expected

```text
SELECTED: SLICE-001
OTHER_ELIGIBLE: SLICE-002
PARALLEL: forbidden
```
