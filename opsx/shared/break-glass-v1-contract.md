# OPSX Break Glass V1 Contract

**Marker (request interface only):** `OPSX_BREAK_GLASS_GO`

**Authority:** Owner-issued grant in `opsx/break-glass/trusted-grants/` (signed with
`OPSX_BREAK_GLASS_ISSUER_HMAC_SECRET`, verified with `OPSX_BREAK_GLASS_VERIFY_HMAC_SECRET`).
Executor runtime must not hold issuer secret (`EXECUTOR_SELF_SIGNING_FORBIDDEN`).
Owner attestation record required under `opsx/break-glass/owner-attestations/`.
Marker text is **not** authorization.

**Exception receipts (never substitute for RED/GREEN):**

- `PROCESS_GATE_BYPASSED`
- `CONTINUE_WITH_EXCEPTION`
- `REVALIDATION_REQUIRED`

**V1 scope freeze completion (distinct from `APPLY_COMPLETE`):**

- `APPLY_COMPLETE_WITH_EXCEPTION` / `DEVELOPMENT_COMPLETE_WITH_EXCEPTION` — real
  implement + contract tests + durable completion receipt under the change root;
  open incident and `OPSX-BG-001` may remain OPEN.
- `CLOSED_VERIFIED` and automatic incident closure are **out of V1 acceptance**
  while `OPSX-BG-001` is `DEFERRED_OUT_OF_SCOPE`.

**Capability backlog (not incident ledger):** `opsx/break-glass/capability-backlog.ndjson`

**Incident ledger (append-only):** `opsx/break-glass/incidents.jsonl`

**V1 pilot consumer:** Split-APPLY selector adapter
(`opsx-break-glass-split-apply-adapter.mjs`) — process gate `APPLY_RED_OPERATIVE` with
`SEMANTIC_ORACLE_UNPROVEN` when classified as OPSX process defect.
