# EVIDENCE_INTEGRITY_ADOPTION_V0

`LIVE_AUTHORITY: NO`. `PLATFORM_ATTESTATION: UNPROVEN` for S-EV1 output.

V0 adoption and verification semantics span multiple slices (S-EV1…S-EV5). **S-EV1 is detect-only** — it never authorizes adoption, never emits `VERIFIED_AT_TIER_T2`, `GREEN`, `PASS`, `apply_complete`, or `completion_class`.

## Owner conditions C1–C10 (S-EV1 binding)

| ID | Rule |
|---|---|
| C1 | Same-path receipt byte change after introducing commit → `RECEIPT_MUTATED_AFTER_STAGE`. Legal supersede is a **new path**; in-place `supersedes` does not clear mutation. |
| C2 | S-EV1 report never emits strong verdict strings; worker receipts claiming them → `OVERCLAIM_VERDICT`. |
| C3 | `owner_attestation_ref` must match S1 prefix regex (`store:`, `cursor-stores/`, `/cursor/stores/`, `owner-message:`). Content is **not** verified → `ATTESTATION_CONTENT_UNVERIFIED` (INFO). |
| C4 | Stage allowlist uses `design.md` at introducing commit (`evidence_integrity_production_surfaces`). No `app/**` default. Product contour: commits touching `opsx/**` or `.cursor/**` → `STAGE_BOUNDARY_VIOLATION` (EI-N26). Control-plane contour exempts `.cursor/pipeline/**`. |
| C5 | `GREEN.red_identity.status` ≠ `RED.status` → `CROSS_RECEIPT_STATUS_MISMATCH` (HARD) even in introducing commit. |
| C6 | Stdout JSON only; `writes=[]`; exit 0 with findings; no ledger/hook/receipt writes. |
| C7 | T2/T3 checks listed in `checks_not_run`; clean S-EV1 ≠ Owner-grade acceptance. |
| C8 | S-EV1 does not implement GREEN freshness or `STALE_GREEN`. |
| C9 | Legacy trees: report-only; `LEGACY_UNBOUND_*` INFO; no verify exception. |
| C10 | Receipt `live_authority` ≠ `NO` or S4-as-authority text → `LIVE_AUTHORITY_CLAIM_FORBIDDEN`. |

## Owner overrides (recorded)

- **D-OWN-2**: P0 witness required for behavioral adoption (not enforced in S-EV1).
- **D-OWN-4**: Prefix regex ≠ Owner authentication. S-EV2 records `owner_authentication=UNPROVEN` and does not treat a prefix match as authentication.
- **D-OWN-6**: Delegated adoption is not implemented in S-EV1 or S-EV2. Per-candidate `owner_attestation_ref` stays structurally required.

## S-EV2 adoption authority

S-EV1 stays detect-only. S-EV2 is a separate checker. It does not emit `VERIFIED_AT_TIER_T2`, `apply_complete`, or `completion_class`.

`slice_class` is read only from the audit field. Legal values are fixed here:

| `slice_class` | `causal_witness.kind` |
|---|---|
| `behavioral` | `PRE_IMPL_REPLAY` or `MUTATION_REVERT`. `NONE` is not `AUDIT_PASS`. |
| `structural_or_config` | `PRE_IMPL_REPLAY`, `MUTATION_REVERT`, or `NONE`. |

Missing or unknown `slice_class` is not `AUDIT_PASS`.

| ID | Rule |
|---|---|
| D-EV2-1 | `slice_class` comes from this contract. Behavioral adoption needs `PRE_IMPL_REPLAY` or `MUTATION_REVERT`. |
| D-EV2-2 | No D-OWN-6 delegation. A scoped grant does not replace per-candidate `owner_attestation_ref`. |
| D-EV2-3 | Auditor `agent_id` must differ from RED, IMPLEMENT, and GREEN writer ids. A missing id is `AUDIT_INCONCLUSIVE`. A matching id is `AUDIT_NOT_INDEPENDENT`. |
| D-EV2-4 | Checker in `.cursor/pipeline/evidence-integrity/`. Smallest `opsx/tools` integration at implement adopt/validate and the refresh guard. Placement: `openspec/changes/opsx-evidence-integrity-and-adoption-v0/decisions/adoption-authority-checker-placement/decision.md`. |
| D-EV2-5 | Legacy v1 authority stays readable. The checker reports `ADOPTION_AUTHORITY_LEGACY` (INFO) and does not emit v2. |
| D-EV2-6 | `opsx-green-refresh-slice.mjs` fails closed instead of stamping NORMAL `RED_PROVEN` / `IMPLEMENTATION_WRITTEN` when the on-disk pair is not already that pair. |

Authority v2 (`schema_version: 2`) requires `candidate_digest`, `audit_receipt_sha256`, `owner_attestation_ref`, and `bindings` of those three. `AUTHORIZE_CURRENT_BYTE_ADOPTION` may remain an invocation string. It is not sufficient. A self-computed `content_hash` is not sufficient.

`IMPLEMENTATION_ADOPTED` under `adoption_contract=EVIDENCE_INTEGRITY_ADOPTION_V0` is authorized only when the checker binds the same digest across the manifest, the audit, the authority, and the implementation pointer. RED status stays `ALREADY_SATISFIED`.

## S-EV1 module

- Input: `opsx.s_ev1_input` v1 (`repo_root`, `change_root`, `contour`, optional `slice_id`, `expect_head_oid`).
- Output: `opsx.s_ev1_report` v1 — schema at `.cursor/pipeline/evidence-integrity/s_ev1_report.schema.json`.
- CLI: `python -m evidence_integrity` with `--input` JSON path (or stdin).

## Production surfaces in `design.md`

```json
{
  "evidence_integrity_production_surfaces": {
    "SLICE-001": ["src/example/module.py"]
  }
}
```

Fenced ` ```json ` block in `openspec/changes/<id>/design.md`. Missing → `ALLOWLIST_UNBOUND` for IMPLEMENT/GREEN path checks.

## Trust ceiling (S-EV1)

`max_tier_reached` is `T0` or `T1` only. See gate `UNPROVEN_TRUST_BOUNDARY` for limits (history rewrite, attestation content, platform attestation, etc.).
