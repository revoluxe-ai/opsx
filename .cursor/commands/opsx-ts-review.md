---
component_id: opsx.commands.ts_review
component_version: 0.1.0
ownership: TARGET_ONLY
runtime_class: ACTIVE_WITH_LIMITS
canonical_source_body: adapted_target_native
required_adapters: ["opsx-lite", "project-generic"]
local_modification_note: "Bounded adaptation from game HEAD 91311ada: sole writer of change-local ts/TS-REVIEW.*; independent of the TS-DESIGN writer; PASS does not unlock design.md because continue/ff/new consumers are unwired."
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
contract_id: OPSX-WB-TS-REVIEW-01
source_snapshot_head: 91311ada29721cfb9aab7a00e1dc4d2a0a8d1982
source_full_file_hash: af8a9dc7e841614a3b014fc00929443fe046d08a2a2184635d9e33eaba97b2d5
description: "Independent review of ts/TS-DESIGN.*; sole writer of ts/TS-REVIEW.*. PASS does not unlock design.md."
argument-hint: "change=<CHANGE_ID>"
---


# PROJECT TARGET PROFILE — REQUIRED

Product-specific execution authority resolves only through:

  opsx/project/capabilities.yaml

Adapter-specific supplements apply only when that adapter is selected in
capabilities.yaml (see opsx/adapters/<adapter-id>/).

Donor literals in this command are historical compatibility text, not authority.
Before product-specific reads, writes, or commands, resolve the required capability.
If unresolved, fail closed with TARGET_CAPABILITY_UNRESOLVED.

Control-plane paths under tools/temporal and tools/cursor_executor are separate
from product runtime adoption.


# /opsx-ts-review

Independent Technical Strategy review for one OpenSpec Change. This command
is the sole writer of `<changeRoot>/ts/TS-REVIEW.md` and
`<changeRoot>/ts/TS-REVIEW.json`.

```text
$ARGUMENTS
```

## Capability and authority

```text
IMPLEMENTATION_STATUS: IMPLEMENTED_UNVERIFIED
LIVE_SLASH_EVIDENCE: NONE
RUNTIME_STATUS: ACTIVE_WITH_LIMITS
CONTRACT_ID: OPSX-WB-TS-REVIEW-01
OWNER_CONTRACT_WRITER: NO
DESIGN_MD_WRITER: NO
TS_DESIGN_WRITER: NO
PASS_WRITER: INDEPENDENT_REVIEW_ONLY
PRODUCT_WRITER: NO
COMMIT_ALLOWED: NO
PRODUCTION_ALLOWED: NO
SOURCE_PARITY: PARTIAL
DESIGN_UNLOCK: NOT_WIRED
CONSUMER_INTEGRATION: MISSING
VERIFIED_STATE: NOT_GRANTED
```

Authority remains:

```text
Owner / owner-contract.md     → product semantics
/opsx-ts-design               → ts/TS-DESIGN.* only; cannot author this review
/opsx-ts-review               → ts/TS-REVIEW.* only
/opsx-new | /opsx-ff | /opsx-continue → design.md writers; they do not read TS-REVIEW
```

A `REVIEW_STATE: PASS` receipt does not unlock `design.md`, apply, or
archive. `/opsx-new`, `/opsx-ff`, and `/opsx-continue` are unwired: this
receipt alone does not authorize them to write `design.md`.

```text
SOURCE_PARITY: PARTIAL
DESIGN_UNLOCK: NOT_WIRED
CONSUMER_INTEGRATION: MISSING
MISSING_CONSUMERS:
- /opsx-new
- /opsx-ff
- /opsx-continue
- /opsx-update
```

## Interface

Only:

```text
/opsx-ts-review change=<CHANGE_ID>
```

| Condition | Status | Reason |
|---|---|---|
| missing or invalid `change=` | `FAIL_CLOSED` | `CHANGE_REQUIRED` / `INVALID_CHANGE_ID` |
| no independent reviewer evidence | `FAIL_CLOSED` | `INDEPENDENT_REVIEW_REQUIRED` |
| reviewer id is the design writer | `FAIL_CLOSED` | `REVIEWER_NOT_INDEPENDENT` |
| evidence claims `review_state` | `FAIL_CLOSED` | `UNTRUSTED_PASS_CLAIM` |

The design writer must not produce the evidence file or the `reviewer_id`.
Forbidden reviewer ids include `opsx-ts-design`, `design-writer`,
`ts-design-writer`, `self`, and `author_agent`.

## Projectization and path resolution

Before any write:

1. Read `opsx/projectization.yaml`.
2. Require `status: COMPLETE` and `normal_commands_enabled: true`; otherwise:

```text
STATUS: FAIL_CLOSED
REASON: OPSX_PROJECTIZATION_REQUIRED
NEXT_ACTION: /opsx-projectize
```

3. Follow `.cursor/commands/opsx-source-resolution.md` and
   `.cursor/commands/opsx-execution-preflight.md`.
4. Run `openspec status --change "<CHANGE_ID>" --json`.
5. Use returned `changeRoot`. Do not invent `openspec/changes/<id>` when
   status resolves a different root.

Read only:

```text
<changeRoot>/ts/TS-DESIGN.json
<changeRoot>/ts/TS-DESIGN.md
<changeRoot>/owner-contract.md
<changeRoot>/proposal.md
<changeRoot>/specs/**/*.md
```

Missing `ts/TS-DESIGN.json` stops before a review write:

```text
STATUS: FAIL_CLOSED
REASON: TS_DESIGN_MISSING
```

## Review states

| State | Meaning |
|---|---|
| `PASS` | Zero blocking findings and the evidence matches the current design hash plus live source inputs |
| `DEFECTS_FOUND` | Review completed with one or more findings |
| `BLOCKED` | Review cannot grant PASS |

`PASS` requires `blocking_finding_count` 0, an empty findings list, design
`ts_status: READY_FOR_REVIEW`, and both hashes current. Any finding,
including `NON_BLOCKING`, is `DEFECTS_FOUND`.

## Finding types

Allowed:

```text
ARCHITECTURE_DEFECT
OWNER_SEMANTIC_GAP
EVIDENCE_GAP
SPEC_DRIFT
SCHEMA_VIOLATION
```

Forbidden finding type: `OWNER_DECISION`.

Each finding requires `id`, `type`, `severity`, `violated_authority`,
`concrete_problem`, `consequence`, `evidence`, `required_correction_class`,
and `target_artifact`. `severity` is `BLOCKING` or `NON_BLOCKING`.
`ARCHITECTURE_DEFECT` targets `ts/TS-DESIGN.json`. `target_artifact:
design.md` is forbidden.

## Independent evidence

Publish only through:

```text
poetry run python .cursor/pipeline/technical-strategy/technical_strategy.py review --change-id "<CHANGE_ID>" --change-root "<changeRoot>" --reviewer-id "<independent-reviewer>" --findings "<evidence.json>"
```

`<evidence.json>` is not an artifact under `changeRoot`. It must be a JSON
object:

```text
reviewer_id
independence = INDEPENDENT_OF_TS_DESIGN_WRITER
bound_ts_design_sha256
bound_input_revision_sha256
findings
```

`bound_ts_design_sha256` is the helper `TS_DESIGN_SHA256` of the current
`ts/TS-DESIGN.json`. `bound_input_revision_sha256` is the input revision
stored on that design. The evidence file must not contain `review_state`,
`verified_state`, `design_unlock`, or `design_md_write`. Those fields are
computed by this command.

Schema:

```text
.cursor/pipeline/technical-strategy/ts_review.schema.json
```

Focused tests:

```text
.cursor/pipeline/technical-strategy-tests/test_ts_review.py
```

## Hash binding

The receipt always records the current file hash and the live source input
identities:

```text
bound_ts_design_sha256 = TS_DESIGN_SHA256(ts/TS-DESIGN.json)
bound_input_revision_sha256 = live owner-contract + proposal + specs
source_input_identities = owner_contract_sha256, proposal_sha256, specs_sha256
```

| Condition | REVIEW_STATE | NEXT_ACTION |
|---|---|---|
| evidence cites another design or input revision | `BLOCKED` | `RECONCILE_TS_DESIGN` (`CROSS_REVISION_REUSE`) |
| `ts_status` is not `READY_FOR_REVIEW` | `BLOCKED` | `REPAIR_TS_DESIGN` |
| design provenance is not the live inputs | `BLOCKED` | `RECONCILE_TS_DESIGN` (`INPUT_REVISION_STALE`) |
| any finding | `DEFECTS_FOUND` | `REPAIR_TS_DESIGN` or `UPSTREAM_SEMANTIC_REALIGNMENT_REQUIRED` |
| zero findings and current binds | `PASS` | `STOP_DESIGN_UNLOCK_NOT_WIRED` |

`OWNER_SEMANTIC_GAP` uses `UPSTREAM_SEMANTIC_REALIGNMENT_REQUIRED`. Do not
ask Owner for a technical fork. Do not patch `design.md` to hide a strategy
defect.

Identical current evidence is idempotent: a second publish keeps the same
bytes. A prior `PASS` whose hashes do not match the current design is not
`CURRENT_PASS`.

## Write boundary

Durable writes:

```text
<changeRoot>/ts/TS-REVIEW.md
<changeRoot>/ts/TS-REVIEW.json
```

Do not write or mutate `ts/TS-DESIGN.*`, `design.md`, `owner-contract.md`,
`proposal.md`, specs, decisions, tasks, or product code.

## Output contract

```text
TS_REVIEW
CHANGE_ID: <change>
REVIEW_STATE: PASS | DEFECTS_FOUND | BLOCKED
BOUND_TS_DESIGN_SHA256: <current design hash>
BOUND_INPUT_REVISION_SHA256: <live input revision>
REVIEWER_ID: <independent reviewer>
FINDING_COUNT: <n>
BLOCKING_FINDING_COUNT: <n>
FILES_CHANGED: ts/TS-REVIEW.md, ts/TS-REVIEW.json
DESIGN_MD_WRITER: NO
TS_DESIGN_WRITER: NO
DESIGN_UNLOCK: NOT_WIRED
CONSUMER_INTEGRATION: MISSING
SOURCE_PARITY: PARTIAL
VERIFIED_STATE: NOT_GRANTED
```

When `REVIEW_STATE` is `PASS`:

```text
NEXT_ACTION: STOP_DESIGN_UNLOCK_NOT_WIRED
SUCCESSOR_COMMAND: /opsx-continue change=<CHANGE_ID>
SUCCESSOR_STATUS: NOT_WIRED
```

Do not invoke `/opsx-continue`, `/opsx-ff`, or `/opsx-new`.

## Failure modes

| Condition | STATUS | REASON |
|---|---|---|
| Design file missing | `FAIL_CLOSED` | `TS_DESIGN_MISSING` |
| Reviewer or evidence missing | `FAIL_CLOSED` | `INDEPENDENT_REVIEW_REQUIRED` |
| Evidence claims PASS | `FAIL_CLOSED` | `UNTRUSTED_PASS_CLAIM` |
| `OWNER_DECISION` or `design.md` target | `FAIL_CLOSED` | no review write |
| Evidence bound to another revision | `BLOCKED` | `CROSS_REVISION_REUSE` |
| Live inputs moved under the design | `BLOCKED` | `INPUT_REVISION_STALE` |

## Self-check before return

- [ ] Wrote only `ts/TS-REVIEW.*` under the status-resolved `changeRoot`
- [ ] Did not mutate `TS-DESIGN.*` or `design.md`
- [ ] Did not accept a design-writer or self-claimed PASS
- [ ] `PASS` only with zero blocking findings and the current input
- [ ] `BOUND_TS_DESIGN_SHA256` matches the current design file
- [ ] Reported `IMPLEMENTED_UNVERIFIED` because no live slash run exists
- [ ] Reported `DESIGN_UNLOCK: NOT_WIRED` and `SOURCE_PARITY: PARTIAL`
