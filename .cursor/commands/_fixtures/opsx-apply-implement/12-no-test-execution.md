# Fixture 12 — no test execution

## Given

`/opsx-apply-implement change=fixture-hydrate` for SLICE-001.

## Expected

```text
TESTS_EXECUTED: false
FORBIDDEN_SUBPROCESSES:
  node --test
  npm test
  pytest
  python -m unittest
  npm run build
  /opsx-browser-check
  /opsx-run-tests
```

Identity hashing of frozen RED test files is allowed and is not test execution.
APPLY GREEN owns all post-implementation runs.
