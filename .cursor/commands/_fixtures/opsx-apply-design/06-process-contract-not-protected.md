# 06 — OPSX process-contract paths are not product protected context

Theme: DESIGN inspected `openspec/decisions/opsx-apply-pipeline-identity/decision.md`
(and sibling `opsx-apply-*` placement decisions) for command semantics.

## Expected

| Check | Result |
|-------|--------|
| `isOpsxProcessContractPath` | true for `openspec/decisions/opsx-apply-*` |
| `collectProtectedContextPaths` | excludes those paths |
| Process decision byte change | **not** `PROTECTED_CONTEXT_DRIFT` |
| Product SoT decision change (`novel-save-sot-api`) | `PROTECTED_CONTEXT_DRIFT` |
| Restore historical process decision bytes | **forbidden** |

`--refresh-identity` rewrites `protected_context_identity` to product paths only
and records `process_contract_identity` as provenance. Do not hand-edit DESIGN.
