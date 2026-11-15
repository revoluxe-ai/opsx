# Fixture 07 — dependency edge without composition contract

## Theme

schema_version 3 READY requires every `slice_order.edges[]` entry to be covered
by `cross_slice_contracts[]` (matching rule:
`dependent_slice_id === edge.to` and `upstream_slice_ids` includes `edge.from`)
plus a complete `composition_completeness` block.

## Given

Take golden `01-ready-valid-change.APPLY-DESIGN.json` and strip composition:

- remove `cross_slice_contracts` (or leave it `[]` while edges remain), and/or
- omit `composition_completeness`

Keep `schema_version: 3`, `status: APPLY_DESIGN_READY`, and the same
`slice_order.edges` (`SLICE-001→003`, `SLICE-002→003`).

## Expected

```text
STATUS: validation FAIL (not APPLY_DESIGN_READY claimable)
REASON: incomplete_composition | schema required field missing
```

Validator:

```bash
node opsx_tools/opsx-apply-design.mjs \
  .cursor/commands/_fixtures/opsx-apply-design/07-edge-without-composition.BAD.json
```

MUST exit non-zero. DESIGN is incomplete: edges exist without WHY/WHAT
composition contracts.

## Negative golden

`07-edge-without-composition.BAD.json` — READY-shaped artifact with edges but
no `cross_slice_contracts` / `composition_completeness`. Intentionally invalid.
