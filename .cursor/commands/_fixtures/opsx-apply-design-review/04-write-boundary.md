# Fixture 04 — write boundary

## Theme

`/opsx-apply-design-review` is read-only vs DESIGN, product, tests, and
planning. Sole writes are change-local review receipts.

## Expected

```text
WRITE_BOUNDARY.product_code: false
WRITE_BOUNDARY.tests: false
WRITE_BOUNDARY.apply_design: false
WRITE_BOUNDARY.planning: false
FILES_CHANGED_PRODUCT: none
FILES_CHANGED_TESTS: none
FILES_CHANGED_APPLY_DESIGN: none
ALLOWED: <changeRoot>/apply-design-review/APPLY-DESIGN-REVIEW.md
ALLOWED: <changeRoot>/apply-design-review/APPLY-DESIGN-REVIEW.json
```

Command must **not** silently repair DESIGN findings inside this hop.
