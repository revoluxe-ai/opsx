# Fixture 02 — write boundary (no product / no tests)

## Given

Same frozen package as fixture 01 (`change=fixture-hydrate`). Executor runs
`/opsx-apply-design change=fixture-hydrate`.

## Expected

```text
STATUS: APPLY_DESIGN_READY
FILES_CHANGED:
  openspec/changes/fixture-hydrate/apply-design/APPLY-DESIGN.md
  openspec/changes/fixture-hydrate/apply-design/APPLY-DESIGN.json
FILES_CHANGED_PRODUCT: none
FILES_CHANGED_TESTS: none
WRITE_BOUNDARY.product_code: false
WRITE_BOUNDARY.tests: false
```

Forbidden outcomes:

- any edit under `src/**`, `backend/**`
- any edit under `tests/**`, `backend/tests/**`
- authored RED test bodies inside `red_test_targets_or_seams`

`red_test_targets_or_seams` may name existing files or seams; they are not
test implementations.
