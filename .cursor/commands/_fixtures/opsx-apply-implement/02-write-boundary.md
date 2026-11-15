# Fixture 02 — write boundary (product yes, tests no)

## Given

`/opsx-apply-implement change=fixture-hydrate` after SLICE-001 `RED_PROVEN`.

## Expected

```text
MAY_WRITE: src/** (within expected_product_write_surfaces), apply-implement/<SLICE-ID>/**
MUST_NOT_WRITE: tests/**, backend/tests/**, apply-red/**, APPLY-DESIGN.json
TESTS_MODIFIED: false
TESTS_EXECUTED: false
```

Forbidden outcomes:

- edit frozen RED test/fixture files
- run `node --test` or any test subprocess
- mark GREEN
