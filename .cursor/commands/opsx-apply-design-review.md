---
component_id: opsx.commands.apply_design_review
component_version: 0.1.0
ownership: TARGET_ONLY
runtime_class: ACTIVE
canonical_source_body: none
required_adapters: ["opsx-lite", "project-generic", "poetry-python"]
local_modification_note: "Independent APPLY DESIGN REVIEW after APPLY_DESIGN_READY and before SCAFFOLD; read-only vs design/product/tests"
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
contract_id: OPSX-GAME-APPLY-DESIGN-REVIEW-01
newline_normalization: none
owner_go: "2026-09-01 Owner DESIGN COMPOSITION + REVIEW"
description: "Independent read-only semantic review of a READY APPLY DESIGN freeze. Writes change-local APPLY DESIGN REVIEW artifacts. Does not edit APPLY DESIGN, product code, or tests."
argument-hint: "change=<change-id>"
---

# STROY-SYSTEM TARGET OVERRIDE - HIGHEST PRIORITY

This command was transferred from a project-specific OPSX realization.
For stroy-system, product-specific realization MUST resolve through:

  opsx/project/capabilities.yaml
  opsx/shared/target-capability-resolution.md

Any inherited donor literal (WB Sky, adv-bot, Poetry, pyproject.toml, app/**,
tests/**, npm/package.json, src/**, public/**, deploy names) is historical or
compatibility text only and is NOT execution authority.

Before any product-specific read/write/command execution, resolve the
corresponding TARGET capability. If unresolved, fail closed:

STATUS: FAIL_CLOSED
REASON: TARGET_CAPABILITY_UNRESOLVED
CAPABILITY: <required capability>
NEXT_ACTION: define/discover capability in opsx/project/capabilities.yaml

Do not infer product stack from the donor repository.
Control-plane runtime paths under tools/temporal and tools/cursor_executor are
separate from product Temporal/runtime adoption.

# TARGET OVERRIDE — HIGHEST PRIORITY

**Contract:** `OPSX-GAME-APPLY-DESIGN-REVIEW-01`
**Runtime class:** `ACTIVE`
**Precedence:** This override wins for target paths, write boundary, capabilities, and bans.

### Purpose (target)

Sole writer of Change-local **APPLY DESIGN REVIEW** receipt:

```text
<changeRoot>/apply-design-review/APPLY-DESIGN-REVIEW.md
<changeRoot>/apply-design-review/APPLY-DESIGN-REVIEW.json
```

Schema: `opsx_tools/registry/apply_design_review.schema.json`
Target runtime contract: `.cursor/pipeline/contracts/SPLIT_APPLY_TARGET_RUNTIME.md`
Validator: `node opsx_tools/opsx-apply-design-review.mjs`

This command answers: given a durable `APPLY_DESIGN_READY` freeze, frozen
planning, and repository reality (clean chat allowed), is the HOW package
composition-complete enough for `/opsx-apply-scaffold`?

It is a **read-only semantic challenge**, not redesign and not product review.
It MUST NOT self-repair APPLY DESIGN.

### Target authority paths

- Changes: `openspec/changes/**` (write under `<changeRoot>/apply-design-review/` only)
- Input (read-only): `<changeRoot>/apply-design/APPLY-DESIGN.json` (+ `.md`)
- Frozen planning under the status-resolved `changeRoot` (read)
- Policies: `openspec/policies/**` (read)
- Decisions: `openspec/decisions/**` (read, context-first)
- Schema: `opsx_tools/registry/apply_design_review.schema.json` (read)
- Composition helpers: `opsx_tools/opsx-apply-composition.mjs` (read)
- Identity: `opsx_tools/opsx-apply-pipeline-identity.mjs` (read)
- Adapter: `opsx/adapters/poetry-python/README.md`
- Quality gates: `opsx/project/quality-gates.md` (read)

### Hard target bans

- Do not install dependencies / modify `pyproject.toml` or `poetry.lock`.
- Do not access production / Kubernetes / secrets.
- Do not push, open PR, rebase, force, or deploy.
- Do not modify `src/**`, `backend/**`, or other production code.
- Do not modify `tests/**`, `backend/tests/**`, or other product tests.
- Do not modify APPLY DESIGN (`apply-design/**`).
- Do not modify planning, specs, tasks, GRAPH, or lifecycle.
- Do not author RED tests, run SCAFFOLD, IMPLEMENT, or GREEN.
- Do not silently repair DESIGN findings inside this command.
- Do not implement `/opsx-apply-scaffold`, `/opsx-apply-red`,
  `/opsx-apply-implement`, `/opsx-apply-green`.
- Do not add Temporal, a generic scheduler/runtime, or apply orchestration.
- Do not require conversational history from the DESIGN chat.
- Mandatory declared executable: none (`npm run build` is not this command's job).

### Projectization gate

Read `opsx/projectization.yaml` before any write.
If `status` != `COMPLETE` or `normal_commands_enabled` is false:

```text
STATUS: FAIL_CLOSED
REASON: OPSX_PROJECTIZATION_REQUIRED
NEXT_COMMAND: /opsx-projectize
```

Named I/O companion: `.cursor/commands/opsx-execution-preflight.md` (Exclusive
active command; Read / tool I/O budgets). Parallel `Read` max **2**. Grep-first.
Source resolution: `.cursor/commands/opsx-source-resolution.md`.

---

# /opsx-apply-design-review

Independent read-only semantic review of APPLY DESIGN.

```text
IMPLEMENTATION_STATUS: IMPLEMENTED
RUNTIME_STATUS: ACTIVE
CONTRACT_ID: OPSX-GAME-APPLY-DESIGN-REVIEW-01
PRODUCT_WRITER: NO
TEST_WRITER: NO
DESIGN_WRITER: NO
RED_WRITER: NO
GREEN_WRITER: NO
SCAFFOLD_WRITER: NO
APPLY_ORCHESTRATOR: NO
TEMPORAL: NOT_IN_THIS_CHANGE
CHAT_HISTORY_REQUIRED: false
```

## Pipeline position

```text
/opsx-apply-design
      ↓
/opsx-apply-design-review   ← THIS COMMAND
      ↓
/opsx-apply-scaffold        (owned by its sibling command)
      ↓
/opsx-apply-red             (owned by its sibling command)
      ↓
/opsx-apply-implement       (owned by its sibling command)
      ↓
/opsx-apply-green           (owned by its sibling command)
      ↓
APPLY_COMPLETE
```

Runs **after** `APPLY_DESIGN_READY` and **before** SCAFFOLD.

Temporal is **not** added. Activity contract:

```text
INPUT:  change_id + APPLY DESIGN identity + planning identity + repo reality
OUTPUT: durable APPLY-DESIGN-REVIEW.md + APPLY-DESIGN-REVIEW.json
TERMINAL: APPLY_DESIGN_REVIEW_PASS | REPAIRS_REQUIRED
```

No hidden dependence on the DESIGN chat. Review may run in a clean context
using durable artifacts + repository only.

## When to use

- APPLY DESIGN is `APPLY_DESIGN_READY`
- Before `/opsx-apply-scaffold`
- After DESIGN was regenerated to consume a prior `repair_handoff`

## When not to use

- Product/spec planning
- Authoring or repairing APPLY DESIGN (use `/opsx-apply-design`)
- Scaffolding, RED, IMPLEMENT, GREEN
- Archive / verify GO

```text
/opsx-apply-design-review change=<change-id>
```

Prefer no extra Owner parameters. Derive store via source-resolution when needed
(`openspec status --change "<id>" --json`, `--store` when applicable).

---

## Strict role boundary

MAY:

- read frozen planning, APPLY DESIGN, git/code/tests, accepted decisions
- run structural composition checks (`structuralDesignReviewFindings`)
- answer review questions 1–12 from durable evidence
- write ONLY its own durable apply-design-review artifacts

MUST NOT:

- modify product code or tests
- modify APPLY DESIGN or planning
- self-repair DESIGN findings
- author RED tests or run SCAFFOLD/IMPLEMENT/GREEN
- treat chat history as authority

---

## Write boundary

**May write:**

```text
<changeRoot>/apply-design-review/APPLY-DESIGN-REVIEW.md
<changeRoot>/apply-design-review/APPLY-DESIGN-REVIEW.json
```

Create `apply-design-review/` if needed. Overwrite on re-run; refresh identity
fields. Persist `REPAIRS_REQUIRED` receipts when `changeRoot` is known.

**Must not write:** production code, tests, APPLY DESIGN, specs, tasks, GRAPH,
lifecycle, other command bodies, `command_activation.ndjson` (install row is a
companion of command creation, not of a later review run).

Machine `write_boundary` MUST be:

```text
product_code: false
tests: false
apply_design: false
planning: false
allowed_paths: the two apply-design-review files above
```

---

## Algorithm

### 0. Projectization

Read `opsx/projectization.yaml`. Fail closed as in TARGET OVERRIDE.

### 1. Resolve change

Require `change=<change-id>` (or a single unambiguous name in `$ARGUMENTS`).
Missing / unresolvable → write `REPAIRS_REQUIRED` with
`INVALID_CHANGE_ID` when `changeRoot` is known; otherwise fail closed without
inventing paths.

```bash
openspec status --change "<change-id>" --json
```

Announce: `Using change: <name>` and how to override.

### 2. Load APPLY DESIGN (required)

Read:

```text
<changeRoot>/apply-design/APPLY-DESIGN.json
```

If missing or `status != APPLY_DESIGN_READY`:

```text
STATUS: REPAIRS_REQUIRED
BLOCK_REASON: DESIGN_NOT_READY
NEXT_ACTION: /opsx-apply-design change=<id>
```

Do **not** invent slices. Do **not** edit DESIGN.

Validate DESIGN with `node opsx_tools/opsx-apply-design.mjs` when available;
schema/composition failure → REPAIRS with `INCOMPLETE_COMPOSITION` (or
`DESIGN_NOT_READY`).

### 3. Bind identities (freshness)

Bind review to:

- `planning_identity.planning_digest` (from DESIGN / recompute over DESIGN
  planning inputs when present)
- `apply_design_identity`:
  - `machine_path`
  - `status` (must be `APPLY_DESIGN_READY` for PASS)
  - `planning_digest`
  - `protected_context_digest`
  - `artifact_sha256` of current APPLY-DESIGN.json bytes
- `protected_context_identity` (product architectural context only)
- optional `process_contract_identity` (OPSX process-contract provenance)

If planning digest or protected product context no longer matches DESIGN →
`STALE_APPLY_DESIGN` / `PROTECTED_CONTEXT_DRIFT` → REPAIRS (do not PASS).

`chat_history_required` is always `false`.

### 4. Architecture Decision Lookup

Context-first paths from the change / DESIGN, then ≤ 3 targeted lookups under
`openspec/decisions/` unless `EVIDENCE_GAP`.

Cite:

- `openspec/decisions/opsx-apply-design-review-placement/decision.md`
- `openspec/decisions/opsx-apply-design-placement/decision.md`
- relevant product decisions for write-path / reuse claims

### 5. Structural composition pre-check

Call `structuralDesignReviewFindings(design)` from
`opsx_tools/opsx-apply-composition.mjs`. Convert returned findings into the
receipt `findings[]` (blocking as appropriate). Agent may add further semantic
findings; tool findings must not be ignored.

### 6. Review questions (mandatory set)

Independently challenge the DESIGN. Answer all of:

1. Is slice decomposition coherent?
2. Are slices too broad or too granular?
3. Are dependency edges complete and correctly directed?
4. Could one slice implement future-slice semantics on an allowed path?
5. Are reusable mechanisms correctly reused rather than duplicated?
6. Does every planned behavior have an implementation owner?
7. Does every critical invariant have a proof owner?
8. Are cross-slice interactions sufficiently modeled?
9. Are special return/control states interpreted consistently downstream?
10. Are concurrency / reentrancy / stale-result cases considered where the
    architecture creates them?
11. Are bootstrap vs already-running semantics distinguished where relevant?
12. Could all slices individually GREEN while the composed behavior remains
    wrong?

**Question 12 is mandatory** on every receipt. Record
`composed_behavior_risk`:

```text
answered: true
all_slices_green_composed_wrong_possible: <boolean>
detail: <why / why not>
```

If Q12 / composed risk shows slices could GREEN while composed behavior stays
wrong → blocking finding `COMPOSED_BEHAVIOR_GAP` → REPAIRS_REQUIRED.

Map other failures to blocker codes when applicable:

| Signal | Code |
|---|---|
| DESIGN not READY | `DESIGN_NOT_READY` |
| DESIGN bytes / planning digest mismatch | `STALE_APPLY_DESIGN` |
| Product protected context drift | `PROTECTED_CONTEXT_DRIFT` |
| Missing / thin cross-slice contracts | `INCOMPLETE_COMPOSITION` |
| Composed-behavior gap (Q12) | `COMPOSED_BEHAVIOR_GAP` |
| Future-slice semantics on current path | `FUTURE_SLICE_LEAK` |
| Duplicate mechanism vs reuse decision | `REUSE_DUPLICATION` |
| Critical invariant lacks proof owner | `MISSING_PROOF_OWNER` |
| Planned behavior lacks implementation owner | `MISSING_IMPLEMENTATION_OWNER` |

### 7. Terminal decision

**PASS** when:

- DESIGN is `APPLY_DESIGN_READY` and identities bind
- no blocking findings
- Q12 answered with verdict
- `composed_behavior_risk.answered === true`
- notes-only findings allowed

```text
status: APPLY_DESIGN_REVIEW_PASS
next_stage: APPLY_SCAFFOLD
```

**REPAIRS** when any blocking finding exists (or DESIGN not reviewable as READY):

```text
status: REPAIRS_REQUIRED
next_stage: APPLY_DESIGN
repair_handoff: { summary, required_design_repairs[] }
blockers: [{ code, detail, ... }]
```

Do **not** edit DESIGN in this hop. Hand off to `/opsx-apply-design`.

### 8. Write artifacts and validate

Write human markdown + machine JSON. Validate with:

```bash
node opsx_tools/opsx-apply-design-review.mjs <path-to-APPLY-DESIGN-REVIEW.json>
```

Schema/semantic failure → do not claim PASS.

Chat output MUST be concise:

```text
STATUS: APPLY_DESIGN_REVIEW_PASS
CHANGE: <id>
REVIEW_ARTIFACT: <changeRoot>/apply-design-review/APPLY-DESIGN-REVIEW.json
BLOCKERS: none
NEXT_ACTION:
  /opsx-apply-scaffold change=<id>
```

On REPAIRS:

```text
STATUS: REPAIRS_REQUIRED
CHANGE: <id>
REVIEW_ARTIFACT: <path>
BLOCKERS: <precise>
NEXT_ACTION:
  /opsx-apply-design change=<id>
  (consume handoff from APPLY-DESIGN-REVIEW.json repair_handoff)
```

Do not emit `/opsx-apply-scaffold` as NEXT_ACTION when REPAIRS_REQUIRED.

---

## Machine artifact (required fields)

See `opsx_tools/registry/apply_design_review.schema.json`.

Successful terminal:

```text
status: APPLY_DESIGN_REVIEW_PASS
next_stage: APPLY_SCAFFOLD
chat_history_required: false
blockers: []
```

Repairs terminal:

```text
status: REPAIRS_REQUIRED
next_stage: APPLY_DESIGN
repair_handoff: { summary, required_design_repairs[] }
blockers: [{ code, detail, ... }]
```

Identity for later stale detection (minimum):

- `planning_identity.planning_digest`
- `apply_design_identity` (machine_path, status, planning_digest,
  protected_context_digest, artifact_sha256)
- `protected_context_identity` (paths[], content_digest)
- optional `process_contract_identity`
- `artifact_identity` (human + machine paths)

SCAFFOLD must treat review as stale when DESIGN `artifact_sha256`, planning
digest, or protected product context digest no longer match the bound review.

---

## Human artifact

`APPLY-DESIGN-REVIEW.md` MUST include:

- Change id, status, generated_at
- Pointer to reviewed APPLY-DESIGN.json + artifact_sha256
- Review questions table (1–12) with verdicts
- Findings (blocking vs note)
- Composed behavior risk (Q12)
- Blockers / repair_handoff when REPAIRS
- Pointer to the JSON path
- `NEXT_STAGE: APPLY_SCAFFOLD` on PASS, or `NEXT_STAGE: APPLY_DESIGN` on REPAIRS

---

## Independence rule

The review command must **not** silently repair DESIGN.

Loop:

```text
/opsx-apply-design-review → REPAIRS_REQUIRED
      ↓
/opsx-apply-design        (consume repair_handoff; regenerate DESIGN)
      ↓
/opsx-apply-design-review (again)
      ↓
PASS → /opsx-apply-scaffold
```

No SCAFFOLD until review PASS.

---

## Interaction with `/opsx-apply`

**This change does not break `/opsx-apply`.** Apply remains the combined
implementation executor until orchestration migrates. Apply MUST NOT require
`APPLY_DESIGN_REVIEW_PASS` in this hop unless a later Owner migration says so.

SCAFFOLD (split pipeline) MUST NOT proceed when review is missing, stale, or
`REPAIRS_REQUIRED` (see pipeline identity gate).

---

## Future Temporal compatibility

Activity-shaped without Temporal:

- Deterministic inputs: `change_id`, DESIGN identity, planning identity
- Deterministic outputs: two files under `apply-design-review/`
- Terminals: `APPLY_DESIGN_REVIEW_PASS` | `REPAIRS_REQUIRED`
- Idempotent: same identities may return existing PASS without re-challenge
- Identities differ → recompute and overwrite
- No requirement that the next stage share this chat

Do not add a worker runtime, queue, or Activity worker in this change.

---

## Architecture Decision References

- `openspec/decisions/opsx-apply-design-review-placement/decision.md` (write path)
- `openspec/decisions/opsx-apply-design-placement/decision.md` (DESIGN SoT)
- `openspec/decisions/opsx-apply-pipeline-identity/decision.md` (freshness / SCAFFOLD gate)
- `openspec/decisions/opsx-local-runtime-kernel/decision.md` (chat is not durable state)

## Ownership & Placement

```text
WHAT: APPLY DESIGN REVIEW receipt (composition challenge, Q1–Q12, freshness bind)
WHO writes: /opsx-apply-design-review only
WHERE: <changeRoot>/apply-design-review/APPLY-DESIGN-REVIEW.md + .json
WHO consumes later: /opsx-apply-scaffold (PASS gate); /opsx-apply-design (repair_handoff)
WHO must not write these files: /opsx-apply, DESIGN/SCAFFOLD/RED/IMPLEMENT/GREEN
WHO must not edit APPLY DESIGN: this command (DESIGN_WRITER: NO)
```

---

## Output contracts

### Success

```text
STATUS: APPLY_DESIGN_REVIEW_PASS
CHANGE: <change-id>
REVIEW_ARTIFACT: <changeRoot>/apply-design-review/APPLY-DESIGN-REVIEW.json
BLOCKERS: none
NEXT_ACTION:
  /opsx-apply-scaffold change=<change-id>
```

### Repairs

```text
STATUS: REPAIRS_REQUIRED
BLOCK_REASON: DESIGN_NOT_READY | STALE_APPLY_DESIGN | PROTECTED_CONTEXT_DRIFT | INCOMPLETE_COMPOSITION | COMPOSED_BEHAVIOR_GAP | FUTURE_SLICE_LEAK | REUSE_DUPLICATION | MISSING_PROOF_OWNER | MISSING_IMPLEMENTATION_OWNER | OPSX_PROJECTIZATION_REQUIRED | INVALID_CHANGE_ID
CHANGE: <change-id or unset>
REVIEW_ARTIFACT: <path or none>
BLOCKERS: <precise>
NEXT_ACTION:
  /opsx-apply-design change=<change-id>
```

### Preflight fragment

```text
EXECUTION_PREFLIGHT:
- git_branch: skipped | ok | deferred_not_required
- git_status: skipped | ok | deferred_not_required
- other_ops: openspec status; load APPLY DESIGN; planning/protected hashes; Q1–Q12
- blocked_on_preflight: true | false
```

---

## Regression fixtures

`.cursor/commands/_fixtures/opsx-apply-design-review/` (when present)

Command-contract tests: `tests/unit/opsxApplyDesignReviewContract.test.mjs`
(when present)

---

## Required companions of this command identity (install hop)

- `opsx_tools/registry/command_activation.ndjson` ADD `opsx-apply-design-review`
- `opsx_tools/registry/apply_design_review.schema.json`
- `opsx_tools/opsx-apply-design-review.mjs`
- `openspec/decisions/opsx-apply-design-review-placement/decision.md`

Do **not** retarget `/opsx-apply` to require this artifact in the same hop.
Do **not** add GRAPH/lifecycle orchestration edges yet.
