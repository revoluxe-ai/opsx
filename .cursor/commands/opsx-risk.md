---
component_id: opsx.commands.risk
component_version: 0.1.0
ownership: TARGET_ONLY
runtime_class: ACTIVE_WITH_LIMITS
canonical_source_body: adapted_target_native
required_adapters: ["opsx-lite", "project-generic"]
local_modification_note: "Bounded adaptation from game HEAD 91311ada: two independent scrutiny profiles, target OCR and scenario contracts, atomic Poetry/Python helper; downstream planning consumers remain unwired."
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
contract_id: OPSX-WB-RISK-01
source_snapshot_head: 91311ada29721cfb9aab7a00e1dc4d2a0a8d1982
source_full_file_hash: 3202ea42ae58331d39ccc524cfd50795a7b6ecdfebf1a9970dc493bfb5fe7c3c
description: "Classify Owner-scrutiny risk for planning or chosen-solution tasking; write one hash-bound Change-local receipt. Never selects checks, writes tasks, or grants GO."
argument-hint: "mode=planning|tasking change=<name> [same-turn pasted OWNER_CONTRACT_REVIEW PASS for planning]"
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


# /opsx-risk

Bounded Owner-scrutiny classifier for an OpenSpec Change.

```text
$ARGUMENTS
```

## Capability and authority

```text
IMPLEMENTATION_STATUS: IMPLEMENTED
RUNTIME_STATUS: ACTIVE_WITH_LIMITS
CONTRACT_ID: OPSX-WB-RISK-01
SUPPORTED_MODES: planning | tasking
CHECK_EXECUTOR: NO
CHECK_SELECTOR: NO
VERIFICATION_MAP_WRITER: NO
GREEN_WRITER: NO
TASK_WRITER: NO
ARCHIVE_VERDICT_WRITER: NO
PRODUCT_REQUIREMENT_WRITER: NO
COMMIT_ALLOWED: NO
PRODUCTION_ALLOWED: NO
```

`RISK_PROFILE` means required **human scrutiny**, not verification depth.
Planning and tasking profiles are classified independently:

```text
planning HIGH does not imply tasking HIGH
planning LOW does not imply tasking LOW
```

Authority remains:

```text
Owner / owner-contract.md       → product semantics
/opsx-owner-contract-review     → semantic review
/opsx-risk mode=planning        → planning scrutiny receipt only
design/specs/decisions          → chosen solution
/opsx-risk mode=tasking         → implementation-boundary scrutiny receipt only
/opsx-continue|ff|update        → planning artifacts and tasks
/opsx-plan-verification         → verification map
/opsx-test                      → GREEN evidence
/opsx-verify                    → archive adequacy verdict
```

## Target adaptation status

SOURCE semantics retained:

- two modes and two independently derived profiles;
- fail closed on missing/stale evidence;
- planning requires current OCR PASS;
- tasking requires current planning-risk plus chosen design/specs and required
  current scenario evidence;
- receipts are bound to exact input identities;
- no check selection, verification execution, task writing, or GO.

Target limitations are explicit:

```text
SOURCE_PARITY: PARTIAL
OCR_DURABLE_PASS_RECEIPT: UNAVAILABLE_IN_TARGET
OCR_PLANNING_INPUT: SAME_TURN_PASTED_PASS_REQUIRED
OWNER_ATTENTION_PROJECTION_CONTRACT: MISSING
DOWNSTREAM_CONSUMER_WIRING: MISSING
MISSING_CONSUMERS:
- /opsx-continue
- /opsx-ff
- /opsx-update
- /opsx-planning-review
```

Therefore the receipts are usable, hash-bound classification artifacts, but
they are not yet automatically enforced by protected planning writers or the
planning reviewer. Never claim full pipeline parity or silently skip risk.

## Interface

Only:

```text
/opsx-risk mode=planning change=<CHANGE_ID>
/opsx-risk mode=tasking change=<CHANGE_ID>
```

For `mode=planning`, the same turn must also contain the complete current
`OWNER_CONTRACT_REVIEW` machine block with:

```text
CHANGE_ID: <same id>
REVIEW_STATE: PASS
VERDICT: PASS
MODE: READ_ONLY
FILES_CHANGED: none
```

Argument gate:

| Condition | Status | Reason |
|---|---|---|
| missing `mode=` | `FAIL_CLOSED` | `MODE_REQUIRED` |
| mode outside `planning|tasking` | `FAIL_CLOSED` | `UNKNOWN_MODE` |
| missing/invalid `change=` | `FAIL_CLOSED` | `CHANGE_REQUIRED` / `INVALID_CHANGE_ID` |
| extra token claims acceptance/verification/check execution | `FAIL_CLOSED` | `UNSUPPORTED_AUTHORITY` |

Never default a mode. `mode=acceptance` and `mode=verification` are unsupported.

## Projectization and path resolution

Before classification:

1. Read `opsx/projectization.yaml`.
2. Require `status: COMPLETE` and `normal_commands_enabled: true`; otherwise:

```text
STATUS: FAIL_CLOSED
REASON: OPSX_PROJECTIZATION_REQUIRED
NEXT_ACTION: /opsx-projectize
```

3. Follow `.cursor/commands/opsx-source-resolution.md` and
   `.cursor/commands/opsx-execution-preflight.md`.
4. Run:

```text
openspec status --change "<CHANGE_ID>" --json
```

Use returned `changeRoot`, `planningHome`, `artifactPaths`, and
`actionContext`. Never invent `openspec/changes/<id>` when status resolves a
different location. Use store selection from the target planning commands when
the Change belongs to a registered standalone store.

5. Require `<changeRoot>/owner-contract.md`.

Read exact paths first; semantic reads are bounded. Hashing the deterministic
tasking bind set is not broad semantic archaeology.

## Write boundary

The invoked mode may write exactly one durable artifact:

```text
mode=planning → <changeRoot>/opsx/planning-risk.md
mode=tasking  → <changeRoot>/opsx/tasking-risk.md
```

Never write or overwrite the other mode’s receipt. Never write
`owner-contract.md`, proposal, decisions, design, specs, scenario evidence,
`tasks.md`, product code, registries, GRAPH, GREEN, or archive evidence during
a command run.

Use:

```text
.cursor/pipeline/risk/risk_receipt.py
.cursor/pipeline/risk-tests/test_risk_receipt.py
```

through `poetry run python`. The helper validates identities and atomically
replaces only the selected receipt. A transient classification payload may
exist only in the OS temporary directory for the helper invocation and must be
deleted before return; it is not durable evidence.

If validation fails, preserve any existing receipt byte-for-byte and report
`RECEIPT_WRITTEN: none`.

## mode=planning

### Required evidence

- current `owner-contract.md`;
- current same-turn pasted target OCR `PASS` for the same Change;
- enough bounded evidence for a defensible profile.

Target OCR is READ_ONLY and emits no hash-bound durable PASS receipt. To avoid
inventing one, this mode requires a fresh OCR invocation/paste in this turn,
then binds the resulting classification receipt to current exact
`owner-contract.md` bytes:

```text
SOURCE_OCR_PASS_RECEIPT: same-turn pasted target OCR PASS
OCR_REVIEWED_ARTIFACT: <resolved owner-contract.md>
SOURCE_OWNER_CONTRACT_SHA256: <sha256 of exact current bytes>
```

Missing prior-chat implication, Owner “GO”, a non-PASS review, wrong Change,
or wrong reviewed artifact is not current OCR proof.

### Classify

Select one evidence-derived planning profile:

- `LOW`: bounded change, few material uncertainty surfaces, and affirmative
  evidence for those boundaries;
- `STANDARD`: material design/boundary questions that planning must resolve;
- `HIGH`: consequential ownership, write authority, shared state,
  irreversibility, authorization, or similarly costly uncertainty.

Absence of evidence is never `LOW`. If evidence cannot support a profile:

```text
STATUS: BLOCKED_NEEDS_INPUT
REASON: INSUFFICIENT_EVIDENCE
RECEIPT_WRITTEN: none
```

Produce only:

- `RISK_TRIGGERS` with path/fact evidence;
- `PLANNING_LENSES`;
- `OWNER_ATTENTION_REQUIREMENTS`;
- `UNRESOLVED_RISK_QUESTIONS`;
- `ESCALATION_CONDITIONS`;
- `RATIONALE`;
- `EVIDENCE`.

Questions do not become requirements. Do not expand the Owner Contract.

### Planning blocks

| Condition | Status | Reason |
|---|---|---|
| owner contract missing | `BLOCKED_NEEDS_INPUT` | `OWNER_CONTRACT_MISSING` |
| same-turn matching OCR PASS absent | `BLOCKED_NEEDS_INPUT` | `OWNER_CONTRACT_REVIEW_NOT_PASS` |
| OCR reviewed artifact differs from current resolved path | `BLOCKED_NEEDS_INPUT` | `OWNER_CONTRACT_REVIEW_STALE` |
| profile lacks affirmative evidence | `BLOCKED_NEEDS_INPUT` | `INSUFFICIENT_EVIDENCE` |
| authoritative inputs materially contradict | `CONFLICTING_EVIDENCE` | `AUTHORITY_CONTRADICTION` |

## mode=tasking

Tasking does not request or mint another OCR PASS. Its semantic chain is the
current planning-risk receipt.

### Required evidence

1. Current `<changeRoot>/opsx/planning-risk.md` with:
   - `MODE: planning`;
   - `STATUS: RISK_CLASSIFIED`;
   - matching `CHANGE_ID`;
   - `SOURCE_OWNER_CONTRACT_SHA256` equal to current owner-contract bytes.
2. Chosen `design.md`.
3. All current Change delta `specs/**/spec.md`; at least one must exist.
4. All Change-owned `decisions/**/*.md` when present.
5. `proposal.md` when present.
6. An explicit scenario applicability decision:
   - `required`: validate current canonical
     `scenario-coverage/plan.md`, `scenarios/<sha256>.md` and `plan.receipt.json` under
     `.cursor/pipeline/contracts/SCENARIO_TEST_COVERAGE.md`;
   - `not-required`: only with artifact-backed reason that scenario planning
     does not apply. Never infer not-required from missing files.
7. Explicit evaluation of every planning `ESCALATION_CONDITION`.

If a planning escalation fired:

```text
STATUS: PLANNING_RISK_REASSESSMENT_REQUIRED
REASON: PLANNING_RISK_ESCALATION_FIRED
NEXT_ACTION: /opsx-risk mode=planning change=<CHANGE_ID>
RECEIPT_WRITTEN: none
```

### Freshness identity

On classification, bind:

```text
SOURCE_OWNER_CONTRACT_SHA256
SOURCE_PLANNING_RISK_RECEIPT
SOURCE_PLANNING_RISK_SHA256
SOURCE_PLANNING_INPUTS
```

`SOURCE_PLANNING_INPUTS` is the complete current solution-defining set, not
only files read semantically:

- `proposal.md` when present;
- `design.md`;
- every `specs/**/spec.md`;
- every `decisions/**/*.md`;
- both scenario plan files when required.

Each entry contains relative `path` and SHA256 of exact bytes. Any changed,
added, or removed bound input makes tasking-risk stale for future consumers.

### Classify independently

- `LOW`: chosen solution is bounded with few sequencing/boundary traps;
- `STANDARD`: notable split, sequencing, or authority-preservation constraints;
- `HIGH`: consequential cutover, atomicity, compatibility, or authority
  boundaries make implementation mistakes costly.

Do not copy the planning profile.

Produce only:

- `RISK_TRIGGERS`;
- `TASKING_LENSES`;
- `IMPLEMENTATION_BOUNDARIES`;
- `SEQUENCING_CONSTRAINTS`;
- `TASK_SPLIT_CONSTRAINTS`;
- `MUST_NOT_COMBINE`;
- `PREREQUISITE_CONSTRAINTS`;
- `OWNER_ATTENTION_REQUIREMENTS`;
- `UNRESOLVED_IMPLEMENTATION_QUESTIONS`;
- `ESCALATION_CONDITIONS`;
- `RATIONALE`;
- `EVIDENCE`.

### TASKING OUTPUT FIREWALL

Every field must express implementation boundaries, sequencing, decomposition,
atomicity, compatibility, or authority only. It must not prescribe:

- commands/checkers to execute;
- test suites or verification depth;
- “tests must pass before done”;
- browser/security/scenario certification;
- a concrete verification technique;
- quality-gate execution as implementation sequencing.

The helper rejects common execution wording. Semantic self-review remains
mandatory; machine filtering is defense-in-depth, not proof of meaning.

### Tasking blocks

| Condition | Status | Reason / route |
|---|---|---|
| planning receipt missing | `BLOCKED_NEEDS_INPUT` | `PLANNING_RISK_RECEIPT_MISSING` → planning mode |
| wrong mode/status/change | `BLOCKED_NEEDS_INPUT` | `PLANNING_RISK_NOT_CLASSIFIED` → planning mode |
| owner-contract hash changed | `BLOCKED_NEEDS_INPUT` | `PLANNING_RISK_PROVENANCE_STALE` → planning mode |
| design/specs missing | `BLOCKED_NEEDS_INPUT` | `CHOSEN_SOLUTION_MISSING` |
| scenario applicability not decided | `BLOCKED_NEEDS_INPUT` | `SCENARIO_REQUIREMENT_DECISION_REQUIRED` |
| required scenario pair missing | `BLOCKED_NEEDS_INPUT` | `SCENARIO_PLAN_EVIDENCE_MISSING` |
| scenario input hash stale | `BLOCKED_NEEDS_INPUT` | `SCENARIO_PLAN_EVIDENCE_STALE` |
| scenario receipt/body invalid | `BLOCKED_NEEDS_INPUT` | `SCENARIO_PLAN_EVIDENCE_INTEGRITY_FAIL` |
| tasking wording chooses checks | `FAIL_CLOSED` | `TASKING_OUTPUT_FIREWALL` |

Scenario evidence recovery:

```text
NEXT_ACTION: /opsx-spec-scenario-coverage mode=plan change=<CHANGE_ID>
```

## Receipt minimums

Planning receipt includes:

```text
COMMAND: /opsx-risk
MODE: planning
CHANGE_ID:
STATUS: RISK_CLASSIFIED
RISK_PROFILE: LOW | STANDARD | HIGH
SOURCE_OCR_PASS_RECEIPT:
OCR_REVIEWED_ARTIFACT:
SOURCE_OWNER_CONTRACT_SHA256:
RISK_TRIGGERS:
PLANNING_LENSES:
OWNER_ATTENTION_REQUIREMENTS:
UNRESOLVED_RISK_QUESTIONS:
ESCALATION_CONDITIONS:
RATIONALE:
EVIDENCE:
NON_CLAIMS:
CONSUMER_INTEGRATION: PARTIAL
```

Tasking receipt includes:

```text
COMMAND: /opsx-risk
MODE: tasking
CHANGE_ID:
STATUS: RISK_CLASSIFIED
RISK_PROFILE: LOW | STANDARD | HIGH
SOURCE_OWNER_CONTRACT_SHA256:
SOURCE_PLANNING_RISK_RECEIPT:
SOURCE_PLANNING_RISK_SHA256:
SCENARIO_EVIDENCE_REQUIREMENT:
SOURCE_PLANNING_INPUTS:
RISK_TRIGGERS:
TASKING_LENSES:
IMPLEMENTATION_BOUNDARIES:
SEQUENCING_CONSTRAINTS:
TASK_SPLIT_CONSTRAINTS:
MUST_NOT_COMBINE:
PREREQUISITE_CONSTRAINTS:
OWNER_ATTENTION_REQUIREMENTS:
UNRESOLVED_IMPLEMENTATION_QUESTIONS:
ESCALATION_CONDITIONS:
RATIONALE:
EVIDENCE:
NON_CLAIMS:
CONSUMER_INTEGRATION: PARTIAL
```

## Chat output

Always emit:

```text
STATUS: RISK_CLASSIFIED | BLOCKED_NEEDS_INPUT | FAIL_CLOSED |
  CONFLICTING_EVIDENCE | PLANNING_RISK_REASSESSMENT_REQUIRED
MODE: planning | tasking
CHANGE_ID:
REASON:
RISK_PROFILE: <only when classified>
RECEIPT_PATH: <selected mode path> | none
RECEIPT_WRITTEN: yes | none
SOURCE_RESOLUTION:
  mode:
  primary_path:
  fallback_used:
  search_operations:
  search_scope:
  unresolved_gap:
CURRENT_STATE:
OWNER_DECISION_REQUIRED:
NEXT_ACTION:
DO_NOT_DO_YET:
- do not treat this receipt as apply, verify, GREEN, or archive GO
- do not choose checks from this profile
- do not rewrite Owner Contract from risk lenses
- do not claim downstream enforcement while consumer wiring is missing
ORIENTATION_HINT:
  NEXT_META: /opsx-meta
```

On planning success:

```text
NEXT_ACTION: /opsx-continue change=<CHANGE_ID>
ALSO_OK: /opsx-ff change=<CHANGE_ID>
LIMITATION: planning-risk consumer gate is not wired in target.
```

On tasking success:

```text
NEXT_ACTION: /opsx-continue change=<CHANGE_ID>
ALSO_OK: /opsx-ff change=<CHANGE_ID>
LIMITATION: tasking-risk set-equality consumer gate is not wired in target.
```

## Self-check

- [ ] Exactly one supported mode and status-resolved Change.
- [ ] Planning used same-turn matching OCR PASS and current owner-contract bytes.
- [ ] Tasking used current planning-risk, chosen design/specs, and explicit
      scenario applicability.
- [ ] Required scenario pair was current and complete before tasking classify.
- [ ] Profiles were derived independently from affirmative evidence.
- [ ] No PASS/LOW by absence.
- [ ] No product semantics, check selection, task list, GREEN, or GO invented.
- [ ] Tasking output firewall passed.
- [ ] Only the invoked mode receipt was written atomically.
- [ ] Missing downstream consumers were reported as partial parity.
