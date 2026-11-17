# OPSX mandatory Architecture Stage V1

**Owner Decision:** every product Change gets a separate Architecture stage before Technical Design. No REUSE/SKIP risk modes: even an unchanged architecture is documented and reviewed. This stage is not the existing optional *architecture dependency evidence* gate.

## Position and source of authority

`owner-contract.md` (reviewed) → proposal/specs/decisions → **`architecture.md` → independent architecture review** → `design.md` → tasks → apply → verify.

OpenSpec's stock `spec-driven` schema does not track `architecture.md`. OPSX enforces the stage as an **additional mandatory artifact and readiness gate** without falsifying OpenSpec `status`. All clients (`/opsx-continue`, `/opsx-ff`, `/opsx-propose`, and equivalent skills), updates, preflights and planning review obey this contract. The OpenSpec `design` artifact becoming `ready` is NOT permission to write it before this gate. A stock OpenSpec `status: done` is insufficient for OPSX `READY`.

## Architecture vs Technical Design

| Architecture (what system must structurally guarantee) | Technical Design (how to implement it) |
|---|---|
| Component boundaries, state and authority ownership | Modules, classes, functions, method signatures |
| Canonical Source of Truth, durability and writes | Fields, tables, APIs, transactions and algorithms |
| Data flow, dependencies, idempotency, failure/recovery semantics | Detailed retry/rollback code, schema and migration steps |
| Invariants, interfaces, compatibility and trade-offs | Implementation sequence, tests and observability wiring |

Architecture is constrained by the reviewed Owner Contract and specs. It cannot decide Owner-semantic questions. Design must inherit architecture; a conflict returns to architecture (and Owner if semantics/authority expansion), never silent design-time rearchitecture.

## Mandatory `architecture.md` form

Use exact top-level sections, with substantive content and sources:

```markdown
# Architecture — <change-id>
ARCHITECTURE_STATUS: DRAFT | READY

## Context and constraints
Owner Contract/specs, current components, accepted architecture and repository facts.

## Boundaries and ownership
Subsystems, SoT, data ownership, write authority; if unchanged, name the existing boundaries and evidence.

## Decisions and rationale
Chosen approach, alternatives/trade-offs, decisions reused vs added, no deferred architecture decision.

## Data flow and interfaces
Components, communication, external integration, read/write contracts.

## Invariants and failure modes
Durability, idempotency, concurrency, retries, recovery, rollback, stop conditions, validation approach.

## Compatibility and migration
Existing consumer behavior, cross-version constraints, rollout/migration (or explicit no migration with reason).

## Technical design handoff
Binding decisions, permitted implementation space, required verification, no unresolved architecture choices.
```

The mechanical checker requires each section, no unresolved TODO/TBD, Owner Contract and proposal, and `ARCHITECTURE_STATUS: READY`. It is **not** a semantic reviewer.

## Review authority and evidence

After architecture draft, an **independent reviewer** checks it against Owner/specs, existing ADRs and repo evidence. An author must not review their own architecture. The reviewer may classify `BLOCKED` / return a bounded finding; only a substantively accepted revision gets a record in:

`openspec/changes/<change>/architecture-evidence/ARCHITECTURE-REVIEW.json`

Receipt fields: `schema_version`, `change_id`, `architecture_sha256`, `upstream_sha256`, `verdict: ACCEPTED`, `author_id`, `reviewer_id`, `review_execution_id`, `findings: []`.

Reference checker (from a consumer repository with OPSX submodule at `opsx/core`; in a Core checkout use `tools/openspec/...`):

```bash
python opsx/core/tools/openspec/architecture_stage_gate.py inputs --change <change> --root .
python opsx/core/tools/openspec/architecture_stage_gate.py check --change <change> --root .
```

Reviewer-only explicit evidence recording *after actual review*:

```bash
python opsx/core/tools/openspec/architecture_stage_gate.py record-review --change <change> --root . --author-id <author> --reviewer-id <independent-reviewer> --reviewed-sha256 <architecture_sha256> --review-execution-id <actual-review-run-id>
```

Review recording writes **only** the review receipt, atomically, with freshness check. CLI-supplied reviewer and review execution IDs are *attestations*, not cryptographic authentication. Runtime must not present it as a trusted independent identity proof without an external trusted execution source; this V1 gate proves claimed separation and digest-bound freshness only.

Changing upstream Owner/proposal/spec/decision or `architecture.md` makes the review stale. The gate fails closed until rereview; cannot silently treat old evidence as accepted. `READY` is not a claim of successful product execution.

## Transition rules

- Architecture is always mandatory for Change; there is no skip, including "simple" or "REUSE".
- `/opsx-new` scaffolds change; `/opsx-continue` creates Owner Contract and upstream artifacts, then creates and gets Architecture reviewed **before** `design.md`.
- `/opsx-ff` and `/opsx-propose` must pause at independent architecture review if not already evidenced; never bypass because OpenSpec reports design ready or skippable.
- `/opsx-update` repairing architecture invalidates downstream readiness until rereview and revalidation.
- Technical Design must declare `ARCHITECTURE_SHA256: <exact SHA-256 of accepted architecture.md>` near `DESIGN_STATUS: READY`. This binding is machine-checked. If Architecture changes after Design, design is stale even if Architecture receives a new review; revalidate and regenerate design as necessary. Technical Design may not select new SoT, ownership, write authority or architecture in contradiction to reviewed architecture. This is `ARCHITECTURE_CONFLICT`, not a design decision.
- `/opsx-planning-review`, tasks READY and `/opsx-apply` require current Architecture Gate. Missing/stale review is `ARCHITECTURE_STAGE_BLOCKED`.
- Applies to existing active changes too: no implicit grandfathering / automatic fake acceptance.
- Owner intervention only for semantic ambiguity, authority expansion, or unresolved conflict. Routine technical decisions can be resolved by architect and independently reviewed.

## Testable failure cases

Absent artifact, empty mandatory section, unreviewed draft, self-review, missing/invalid receipt, changed owner-contract/proposal/spec/decisions, changed architecture revision, different Change identity, path traversal/symlink, fast-forward bypass, stale design/tasks, apply without Architecture review. Mechanical checker returns non-zero and actionable finding codes. Substantive architectural correctness still requires review.
