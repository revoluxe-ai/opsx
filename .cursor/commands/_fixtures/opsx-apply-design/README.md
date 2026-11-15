# Fixtures: /opsx-apply-design

Process-only regression cases for APPLY DESIGN. Command under test:
`/opsx-apply-design`.

These fixtures do **not** authorize product or test writes. A correct executor
MUST emit the `Expected` status and MUST NOT modify `src/**` / `tests/**`.

Golden machine artifacts (`*.APPLY-DESIGN.json`) MUST validate:

```bash
node opsx_tools/opsx-apply-design.mjs <golden.json>
```

Negative / intentional-invalid artifacts (`*.BAD.json`) MUST fail that
validator.

## Index

| ID | File | Theme | Expected STATUS |
|----|------|-------|-----------------|
| 01 | `01-ready-valid-change.md` | frozen package + repo reality; reuse; grouped + split slices; order graph; composition contracts (schema_version 3); next_stage APPLY_DESIGN_REVIEW; scaffold_contract; no chat history | APPLY_DESIGN_READY |
| 02 | `02-write-boundary.md` | command does not modify product code or tests | APPLY_DESIGN_READY (writes apply-design only) |
| 03 | `03-planning-reality-conflict.md` | architectural contradiction | BLOCKED / PLANNING_REALITY_CONFLICT |
| 04 | `04-missing-planning-authority.md` | missing tasks.md / WHAT package | BLOCKED / MISSING_PLANNING_AUTHORITY |
| 05 | `05-existing-apply-compat.md` | `/opsx-apply` remains operational; no APPLY_DESIGN_READY gate | n/a (apply contract unchanged) |
| 06 | `06-process-contract-not-protected.md` | OPSX process decisions are not product protected context | process drift ≠ PROTECTED_CONTEXT_DRIFT |
| 07 | `07-edge-without-composition.md` | dependency edges without cross_slice_contracts / composition_completeness | validate FAIL (`*.BAD.json`) |
| 08 | `08-uncovered-critical-interaction.md` | locally complete slices; missing critical interaction treatment | DESIGN_REVIEW → REPAIRS_REQUIRED |
| 09 | `09-dogfood-failure-classes.md` | five generic composition failure classes as contract patterns | checklist / pattern SoT |

## Static matrix

| Requirement | Fixture |
|-------------|---------|
| Valid frozen change → READY | 01 |
| schema_version 3 + composition | 01 |
| No product writes | 02, 01 `write_boundary` |
| No test writes | 02, 01 `write_boundary` |
| Explicit reuse decisions | 01 |
| Multiple tasks → one slice | 01 SLICE-001 |
| Independent responsibilities → separate slices | 01 SLICE-002 vs SLICE-003 |
| Durable slice_order graph | 01 |
| Edge coverage via cross_slice_contracts | 01 CSC-003; negative 07 |
| PLANNING_REALITY_CONFLICT | 03 |
| Missing planning authority | 04 |
| next_stage = APPLY_DESIGN_REVIEW | 01 |
| chat_history_required false | 01, 03, 04 |
| Existing `/opsx-apply` operational | 05 |
| Uncovered critical interaction → review repairs | 08 |
| Dogfood failure-class patterns | 09 |
