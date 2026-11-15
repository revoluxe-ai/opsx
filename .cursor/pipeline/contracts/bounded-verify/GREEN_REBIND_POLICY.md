# GREEN rebind safety (bounded-verify consumer policy)

## Facts (native Split-APPLY)

- `refresh-identity` / `--rebind-downstream` in `opsx-apply-design.mjs` may rewrite GREEN receipt envelopes.
- `classifyDesignIdentityDelta` classifies edits: `PLANNING`, `SLICE_SEMANTIC`, `PRODUCT_ARCHITECTURE` → **not** `rebind_safe`.
- Only `PROCESS_CONTRACT_ONLY` may be rebind-safe; behavior-changing design edits must invalidate evidence.

## Bounded-verify requirements

1. **No bulk rebind** from design hash alone — per-slice `classifyGreenEvidenceFreshness` is authoritative.
2. **Status-only GREEN** stubs are not sufficient for production split-apply changes with nested `*_identity` fields.
3. **STALE_GREEN** in native tooling must surface as `SPLIT_APPLY_STALE` in bounded snapshot / preflight (not `NOT_APPLICABLE`).
4. Historical FAIL / stale GREEN artifacts remain on disk; recovery requires explicit `authorize_check_recovery`.
5. **Exception route:** open Owner break-glass + `PROCESS-GATE-DEFERRAL.json` allows compose/DRAFT via `OWNER_OVERRIDDEN` without reclassifying stale GREEN as CURRENT (see `BREAK_GLASS_EXCEPTION_ROUTE.md`).

## Owner decision

Automated mass rebind after design mutation without per-slice equivalence proof is **forbidden** for verification completion.
