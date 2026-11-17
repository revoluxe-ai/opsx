---
name: "/opsx-architecture-review"
id: "opsx-architecture-review"
category: "Workflow"
description: "Independent Architecture review for mandatory OPSX stage before Technical Design"
---

# /opsx-architecture-review <change>

**Source of truth:** `.cursor/pipeline/contracts/ARCHITECTURE_STAGE_V1.md`.

Review architecture **independently** from its author before `design.md` creation. Review is not projectization, Owner-contract approval, or the existing dependency/library compatibility gate.

1. Locate change from the actual OpenSpec root. Read the reviewed Owner Contract, proposal, all specs/decisions and `architecture.md`, then inspect bounded repository evidence and relevant accepted ADRs.
2. Check component boundaries, responsibilities, SoT, write authority, data flow, durability, state/concurrency, idempotency, retries, recovery, cross-component contracts, compatibility, failure modes, implementation constraints and testability. A no-change architecture must still name and justify existing decisions.
3. Compare normative semantics to Owner Contract and existing accepted architecture. No new Owner decisions or authority expansion are permitted without the corresponding Owner gate.
4. Produce `BLOCKED` with specific causal findings and next repair boundary if any gap, ambiguity, stale input or contradiction exists. Do not record acceptance.
5. When substantively ready, obtain the current digest using:
   ```bash
   python opsx/core/tools/openspec/architecture_stage_gate.py inputs --root . --change <change>
   ```
6. After an actual independent review and permitted evidence-only write, record the accepted revision:
   ```bash
   python opsx/core/tools/openspec/architecture_stage_gate.py record-review --root . --change <change> --author-id <writer-id> --reviewer-id <different-reviewer-id> --reviewed-sha256 <digest-from-inputs> --review-execution-id <actual-review-run-id>
   ```
   Do not invent identities, review steps or successful outcomes. IDs are declarative and must not be described as cryptographic authentication.
7. Verify via `architecture_stage_gate.py check --root . --change <change>`. Only `READY` permits Technical Design. All writes are restricted to the review receipt. Never write product code, specs or design under review.

For subsequent modification of owner/proposal/specs/decisions/architecture, the review is stale and must run again.

Output: `ARCHITECTURE_REVIEW: ACCEPTED | BLOCKED`, change id, architecture digest, upstream digest, findings, affected boundaries, evidence path, next action.
