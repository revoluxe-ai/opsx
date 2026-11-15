# Fixture 15 — test content identity before and after writes

## Given

SLICE-001 RED_PROVEN with frozen `test_content_identity`. IMPLEMENT writes
`src/services/hydrateCoordinator.js` only.

## Expected

```text
BEFORE_WRITES: hash frozen test_paths + fixture_paths; must equal RED.test_content_identity
AFTER_WRITES: re-hash the same paths (read-only); must equal BEFORE
MISMATCH_BEFORE: BLOCKED STALE_RED (do not start product writes)
MISMATCH_AFTER: BLOCKED FROZEN_RED_MUTATED (do not rewrite the test)
NOT_TEST_EXECUTION: true
```
