# Fixture 02 — write boundary

## Given

`/opsx-apply-red change=fixture-hydrate` after valid APPLY DESIGN.

## Expected

```text
PRODUCT_FILES_MODIFIED: false
FILES_CHANGED_PRODUCT: none
MAY_WRITE: tests/unit/**, backend/tests/unit/**, apply-red/<SLICE-ID>/**
MUST_NOT_WRITE: src/**, backend/app/**, apply-design/APPLY-DESIGN.json
```
