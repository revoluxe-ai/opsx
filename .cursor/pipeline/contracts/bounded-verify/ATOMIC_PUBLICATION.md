# Bounded Verify — Atomic Publication (Protocol A)

## Module

`.cursor/pipeline/bounded-verify/bounded_verify_atomic.py`

Used by `bounded_verify_plan` (SLICE-001) and later ledger/result publishers.

## Plan publication steps

1. Write complete payload to temp sibling: `plan.json.tmp.<pid>.<nonce>`
2. `flush` + `fsync`
3. Parse + JSON-schema validate
4. `os.replace(temp, plan.json)`

## Reader rules

- Temp siblings (`*.tmp.*`) are **never** canonical authority.
- Absent, malformed, or schema-invalid `plan.json` is treated as **no valid plan**.
- `plan_status=FROZEN` without valid `plan_fingerprint` is **not** accepted.

## Crash semantics

| Phase | Canonical authority |
|-------|---------------------|
| Before `os.replace` | Prior `plan.json` or absent |
| After `os.replace` | Complete valid new plan |

Partial temp files may remain on disk; readers ignore them.

## Forbidden

- Publishing partial JSON to the canonical path
- Force/regenerate/overwrite-frozen shortcuts
- Using Git HEAD as `plan_fingerprint`
