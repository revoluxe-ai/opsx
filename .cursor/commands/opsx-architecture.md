---
name: "/opsx-architecture"
id: "opsx-architecture"
category: "Workflow"
description: "Mandatory Architecture stage for an existing product Change, before Technical Design"
---

# /opsx-architecture <change>

**Source of truth:** `.cursor/pipeline/contracts/ARCHITECTURE_STAGE_V1.md`.

Scope: planning-only; no product code, tests, deploy, implementation, or silent Owner-semantic change. Use only an existing OpenSpec Change with a reviewed Owner Contract, proposal and available specs/decisions. If they are absent or uncertain, stop and use the appropriate upstream planning workflow.

1. Resolve OpenSpec changeRoot through the active schema/store; ensure it belongs to the authorized planning contour.
2. Inspect Owner Contract, proposal, specs, accepted architecture decisions and actual repository facts. Separate FACT, INFERENCE, PROPOSAL and OWNER DECISION.
3. Create or revise `<changeRoot>/architecture.md` with the seven required sections from the contract. Explicitly choose component boundaries, ownership, SoT, interfaces, failure/retry/recovery and compatibility. For unchanged architecture, still document and justify what is preserved with repository evidence. Do not duplicate low-level Technical Design.
4. Set `ARCHITECTURE_STATUS: READY` only when choices are resolved and the text contains no unapproved semantic or authority expansion. Else `ARCHITECTURE_STATUS: DRAFT` and PAUSE with unresolved findings. This self-declared status is **not** accepted until independently reviewed.
5. Route to separate `/opsx-architecture-review <change>`. Do not self-review, manufacture an accepted receipt or write `design.md`/tasks before the independent review.
6. Run `python opsx/core/tools/openspec/architecture_stage_gate.py check --root . --change <change>` from the consumer repo to verify. When working directly in the OPSX Core checkout instead of a consumer, the script is at `tools/openspec/architecture_stage_gate.py`.
7. On failed review, repair architecture only within authorized planning scope and request rereview. On success, `/opsx-continue` may proceed to Technical Design.

Output: `ARCHITECTURE_STATUS: DRAFT | READY`, change ID, decisions, remaining questions, review status, next action.
