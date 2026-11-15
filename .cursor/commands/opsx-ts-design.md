---
component_id: opsx.commands.ts_design
component_version: 0.1.0
ownership: TARGET_ONLY
runtime_class: ACTIVE_WITH_LIMITS
canonical_source_body: adapted_target_native
required_adapters: ["opsx-lite", "project-generic"]
local_modification_note: "Bounded adaptation from game HEAD 91311ada: sole writer of change-local ts/TS-DESIGN.*; owner-contract and design.md stay with their target owners; planning-risk is not a strategy bind; /opsx-ts-review is not activated."
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
contract_id: OPSX-WB-TS-DESIGN-01
source_snapshot_head: 91311ada29721cfb9aab7a00e1dc4d2a0a8d1982
source_full_file_hash: 5e690dc840407b1280872007bf2a64d14b57ef8802a2e4f839a3a327d9242c7a
description: "Draft a change-local Technical Strategy bound to current owner-contract, proposal, and specs. Never writes design.md, TS-REVIEW, or PASS."
argument-hint: "change=<CHANGE_ID> [mode=draft|reconcile]"
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

# /opsx-ts-design

Draft the Technical Strategy for one OpenSpec Change after `proposal.md`
and `specs/**/*.md` exist. This command is the sole writer of
`<changeRoot>/ts/TS-DESIGN.md` and `<changeRoot>/ts/TS-DESIGN.json`.

```text
$ARGUMENTS
```

## Capability and authority

```text
IMPLEMENTATION_STATUS: IMPLEMENTED_UNVERIFIED
LIVE_SLASH_EVIDENCE: NONE
RUNTIME_STATUS: ACTIVE_WITH_LIMITS
CONTRACT_ID: OPSX-WB-TS-DESIGN-01
SUPPORTED_MODES: draft | reconcile
OWNER_CONTRACT_WRITER: NO
DESIGN_MD_WRITER: NO
PLANNING_RISK_WRITER: NO
REVIEW_STATE_WRITER: NO
TS_REVIEW_ACTIVATED: NO
PASS_WRITER: NO
PRODUCT_WRITER: NO
COMMIT_ALLOWED: NO
PRODUCTION_ALLOWED: NO
SOURCE_PARITY: PARTIAL
PLANNING_RISK_BIND: NOT_REQUIRED
CONSUMER_INTEGRATION: MISSING
```

Authority remains:

```text
Owner / owner-contract.md     → product semantics
/opsx-new | /opsx-update      → owner-contract repair
proposal.md + specs           → upstream planning inputs, read-only here
/opsx-ts-design               → ts/TS-DESIGN.* only
/opsx-update | /opsx-ff | /opsx-continue → design.md and other planning artifacts
/opsx-ts-review               → not activated; do not invoke it
```

Do not rewrite `owner-contract.md`, `proposal.md`, `specs/**`, `design.md`,
decisions, tasks, planning-risk, or tasking-risk. Do not mint
`REVIEW_STATE: PASS`. Do not ask Owner a technical tie-breaker. Class B
semantic gaps route `UPSTREAM_SEMANTIC_REALIGNMENT_REQUIRED`.

## Target adaptation status

SOURCE interface retained:

- explicit name `/opsx-ts-design`;
- `change=<CHANGE_ID>` and `mode=draft|reconcile`;
- one `strategy_class` from `NEW_ARCHITECTURE`,
  `EXTEND_EXISTING_ARCHITECTURE`, `EXISTING_ARCHITECTURE_REUSED`,
  `NO_MATERIAL_ARCHITECTURAL_CHANGE`;
- `READY_FOR_REVIEW` requires two candidates and one rejection when the
  class is new or extended, or `new_architectural_boundary` is true;
- migration `REQUIRED` requires the seven migration keys;
- `UNKNOWN` and `DEFINED` placeholders cannot be published;
- compatibility `IMPACT` requires `compatibility_risks` and
  `mitigation_strategy`;
- write boundary is only `ts/TS-DESIGN.md` and `ts/TS-DESIGN.json`;
- `TS_DESIGN_SHA256` is the helper canonical hash, not a PASS receipt.

Target limitations are explicit:

```text
SOURCE_PARITY: PARTIAL
PLANNING_RISK_BIND: NOT_REQUIRED
```

SOURCE schema also records `planning_risk_sha256`. This target does not.
Planning-risk is an independent scrutiny receipt and is not strategy or
`design.md` authority. The helper rejects that key.

```text
CONSUMER_INTEGRATION: MISSING
MISSING_CONSUMERS:
- /opsx-ts-review
- /opsx-continue
- /opsx-ff
- /opsx-update
```

A published `READY_FOR_REVIEW` does not unlock `design.md`, apply, or
archive. Those writers were not modified and do not read `TS-DESIGN.*`.

`/opsx-ts-review` was not implemented in this activation.
`TS_REVIEW_ACTIVATED: NO`. Do not claim that review passed.

## Interface

Only:

```text
/opsx-ts-design change=<CHANGE_ID>
/opsx-ts-design change=<CHANGE_ID> mode=draft
/opsx-ts-design change=<CHANGE_ID> mode=reconcile
```

Omitted mode means `draft`.

| Condition | Status | Reason |
|---|---|---|
| missing or invalid `change=` | `FAIL_CLOSED` | `CHANGE_REQUIRED` / `INVALID_CHANGE_ID` |
| mode outside `draft\|reconcile` | `FAIL_CLOSED` | `UNKNOWN_MODE` |
| token asks to write design, review, PASS, or product code | `FAIL_CLOSED` | `UNSUPPORTED_AUTHORITY` |

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

Require, and only read:

```text
<changeRoot>/owner-contract.md
<changeRoot>/proposal.md
<changeRoot>/specs/**/*.md
```

Missing specs or proposal stops before a strategy write:

```text
STATUS: BLOCKED
REASON: UPSTREAM_SPECS_MISSING
```

Use `UPSTREAM_SPECS_MISSING` for a missing proposal as well. Do not create
`ts/` in that state.

## Strategy rules

Exactly one `strategy_class`. For `READY_FOR_REVIEW`, `NEW_ARCHITECTURE`,
`EXTEND_EXISTING_ARCHITECTURE`, or `new_architectural_boundary: true`
requires `candidates` minItems 2 and `rejected_strategies_with_reason`
minItems 1.

`NO_MATERIAL_ARCHITECTURAL_CHANGE` may use one candidate. A
`NOT_APPLICABLE` core decision or assessment needs `waiver_evidence` of at
least 10 characters before `READY_FOR_REVIEW`.

| Class | Meaning | Route |
|---|---|---|
| A | Technical defect in the strategy payload | repair this command |
| B | Owner semantic gap | `UPSTREAM_SEMANTIC_REALIGNMENT_REQUIRED` |
| C | Evidence gap | `EVIDENCE_GAP`; do not ask Owner for a technical fork |

`ts_status` is only `DRAFT`, `READY_FOR_REVIEW`, or `BLOCKED`.
`BLOCKED` requires `block_reason` `UPSTREAM_SEMANTIC_REALIGNMENT_REQUIRED`
or `EVIDENCE_GAP`.

`migration_assessment.status` `REQUIRED` needs:

```text
persisted_representation_change
existing_data_impact
migration_direction
deployment_ordering
compatibility_window
rollback_implications
verification_obligations
```

`UNKNOWN` and `DEFINED` are forbidden. `backward_compatibility_assessment`
`IMPACT` needs `compatibility_risks` and `mitigation_strategy`.

## Write boundary

Durable writes:

```text
<changeRoot>/ts/TS-DESIGN.md
<changeRoot>/ts/TS-DESIGN.json
```

The transient payload JSON stays outside `changeRoot` and is not an
artifact. Publish only through:

```text
poetry run python .cursor/pipeline/technical-strategy/technical_strategy.py design --change-id "<CHANGE_ID>" --change-root "<changeRoot>" --payload "<temp-payload.json>" --requested-status DRAFT|READY_FOR_REVIEW|BLOCKED
```

Add `--block-reason` only for `BLOCKED`.

Read freshness without writing:

```text
poetry run python .cursor/pipeline/technical-strategy/technical_strategy.py freshness --change-id "<CHANGE_ID>" --change-root "<changeRoot>"
```

Schema:

```text
.cursor/pipeline/technical-strategy/ts_design.schema.json
```

Focused tests:

```text
.cursor/pipeline/technical-strategy-tests/test_ts_design.py
```

`mode=draft` publishes the requested status bound to live bytes.
`mode=reconcile` runs `freshness` first. `STALE` or `ABSENT` may be
republished from the current inputs. A payload whose
`upstream_provenance` does not match those live bytes is
`INPUT_REVISION_STALE` and must not overwrite the existing pair.
Identical inputs are idempotent: a second `design` publish keeps the
same bytes and `TS_DESIGN_SHA256`.

Do not run the helper `review` action. It returns
`TS_REVIEW_NOT_ACTIVATED`.

## Output contract

```text
TS_DESIGN
CHANGE_ID: <change>
TS_STATUS: DRAFT | READY_FOR_REVIEW | BLOCKED
STRATEGY_CLASS: NEW_ARCHITECTURE | EXTEND_EXISTING_ARCHITECTURE | EXISTING_ARCHITECTURE_REUSED | NO_MATERIAL_ARCHITECTURAL_CHANGE
TS_DESIGN_SHA256: <canonical hash>
RECOMMENDED_STRATEGY_ID: <id>
INPUT_REVISION_SHA256: <live bind>
FRESHNESS: CURRENT
FILES_CHANGED: ts/TS-DESIGN.md, ts/TS-DESIGN.json
DESIGN_MD_WRITER: NO
OWNER_CONTRACT_WRITER: NO
REVIEW_STATE_WRITER: NO
TS_REVIEW_ACTIVATED: NO
VERIFIED_STATE: NOT_GRANTED
SOURCE_PARITY: PARTIAL
CONSUMER_INTEGRATION: MISSING
```

When `TS_STATUS` is `READY_FOR_REVIEW`, name the successor and stop.
Do not invoke it:

```text
SUCCESSOR_COMMAND: /opsx-ts-review change=<CHANGE_ID>
SUCCESSOR_STATUS: NOT_ACTIVATED
NEXT_ACTION: STOP_TS_REVIEW_NOT_ACTIVATED
```

For `DRAFT` or `BLOCKED`:

```text
NEXT_ACTION: repair ts-design or the named upstream gap
```

## Failure modes

| Condition | STATUS | REASON |
|---|---|---|
| Specs or proposal missing | `BLOCKED` | `UPSTREAM_SPECS_MISSING` |
| Owner semantic gap | `BLOCKED` | `UPSTREAM_SEMANTIC_REALIGNMENT_REQUIRED` |
| Evidence missing | `BLOCKED` | `EVIDENCE_GAP` |
| Claimed bind does not match live files | `FAIL_CLOSED` | `INPUT_REVISION_STALE` |
| Placeholder or forbidden key | `FAIL_CLOSED` | helper error; no `ts/` write |

## Self-check before return

- [ ] Wrote only `ts/TS-DESIGN.*` under the status-resolved `changeRoot`
- [ ] Did not write `TS-REVIEW.*`, `design.md`, or owner-contract
- [ ] Did not mint PASS and did not run `/opsx-ts-review`
- [ ] `TS_DESIGN_SHA256` came from the helper
- [ ] Freshness is `CURRENT` for the bytes just written
- [ ] Class B used `UPSTREAM_SEMANTIC_REALIGNMENT_REQUIRED`
- [ ] Reported `IMPLEMENTED_UNVERIFIED` because no live slash run exists
