---
component_id: opsx.commands.bug_family_plan
component_version: 0.1.1
ownership: TARGET_ONLY
runtime_class: ACTIVE_WITH_LIMITS
source_commit: 91311ada29721cfb9aab7a00e1dc4d2a0a8d1982
source_path: .cursor/commands/opsx-bug-family-plan.md
required_adapters: ["opsx-lite", "project-generic"]
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
contract_id: OPSX-WB-BUG-FAMILY-PLAN-01
---


# TARGET OVERRIDE — HIGHEST PRIORITY

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


# /opsx-bug-family-plan

Produce a bounded, read-only pre-repair grouping plan from `CLEAN_FINDINGS`.
The plan preserves the SOURCE distinction between `BUG_FAMILY` and
`ATOMIC_BUG`, but it is deliberately non-durable: target `family/**` artifacts
belong to `/opsx-bug-team` post-GREEN invariant fanout and must not be reused as
a competing pre-repair store.

## Authority and safety

```text
IMPLEMENTATION_STATUS: IMPLEMENTED_WITH_LIMITS
RUNTIME_STATUS: ACTIVE_WITH_LIMITS
CONTRACT_ID: OPSX-WB-BUG-FAMILY-PLAN-01
MODE: READ_ONLY
READ_ONLY: true
SOURCE_PARITY: NON_DURABLE_PLANNING_SUBSET
GO_SUPPORTED: false
TARGET_FAMILY_WRITER: /opsx-bug-team
CASE_LEDGER_WRITER: /opsx-materialize-bug-queue
COMMIT_ALLOWED: NO
PRODUCTION_WRITES_ALLOWED: NO
```

- Do not create SOURCE-style `opsx/runs/bug-cases/family-plans/**`.
- Do not create or edit target case `family/**`, `INDEX.md`, `LEDGER.md`,
  findings, intakes, route traces, OpenSpec, product code, or tests.
- `BUG_FAMILY` in this command means a non-durable pre-repair analysis group.
  It is not target `BUG_FAMILY_ID`, `FAMILY-FANIN`, or
  `SAME_INVARIANT_FINDING`.
- Do not fix, classify, claim, materialize, or invoke another command.
- Follow exact-path-first bounded-read rules from source resolution and
  execution preflight.

## User input

```text
$ARGUMENTS
```

Supported SOURCE tokens:

```text
case=<CASE_ID>
plan=<PLAN_ID>
finding=<FINDING_ID>
limit=<N>
GO
```

Target routing also accepts:

```text
change=<CHANGE_ID>
```

Input findings must come from an inline or immediately preceding
`CLEAN_FINDINGS` block. A `plan=` value is a correlation label only; it does
not resolve a durable plan file in this adaptation.

`GO` returns `GO_UNSUPPORTED` with the same plan. It never writes a plan.

### Mandatory input gate — FIRST ACTION

Before any tool call, repo read, case lookup, historical lookup, or search:

1. Parse only explicit `$ARGUMENTS` plus an inline/immediately preceding
   `CLEAN_FINDINGS` block.
2. If `GO` is present without a valid clean input set, emit `GO_UNSUPPORTED`
   and STOP without tool calls.
3. If there is no qualifying `CLEAN_FINDINGS` block and no exactly-one grounded
   finding supplied in the current turn, emit `BLOCKED_NEEDS_INPUT` and STOP
   without tool calls.
4. Do not search the repository to manufacture missing findings or ids.

Only after this gate passes may clean-input validation and grouping begin.

## Clean-input gate

Accept input only when:

- `/opsx-bug-intake-clear` emitted `CLEAR_COMPLETE` or `CLEAR_PLAN`; or
- the Owner explicitly labels the inline block `CLEAN_FINDINGS`; or
- exactly one grounded finding is supplied and no duplicate conflict is
  visible.

Unresolved duplicate rows produce `BLOCKED_NEEDS_CLEAN_INPUT` and route back to
`/opsx-bug-intake-clear`. This command does not become a second dedupe owner.

Every input row must include:

```text
LOC_LINK
LITERAL_CLAIM or ROOT_FINGERPRINT
PROVENANCE
REPAIR_AUTHORITY: PRODUCT
```

Process-contract rows are rejected from this product planner.

## Grouping rules

1. Preserve every source finding exactly once in `MEMBERSHIP_TRACE`.
2. A shared file, package, severity, or component is insufficient for a
   `BUG_FAMILY`.
3. Emit a pre-repair `BUG_FAMILY` only when evidence states one shared violated
   system invariant and a common repair closure condition.
4. Record root-cause and invariant relations independently.
5. If common-invariant evidence is uncertain, emit separate `ATOMIC_BUG`
   items. All-atomic is a successful result.
6. A single clean finding is always one `ATOMIC_BUG`; never invent family
   membership.
7. Do not mine historical cases or target post-GREEN family artifacts to force
   a group.

## Work-item shape

```text
WORK_ITEM_TYPE: BUG_FAMILY | ATOMIC_BUG
WORK_ITEM_ID: RUN_LOCAL-WI-<n>
PERSISTENCE: NONE
SOURCE_FINDING_IDS: [...]
PROVENANCE: [...]
FAMILY_KIND: PRE_REPAIR_ANALYSIS_ONLY
ROOT_CAUSE_RELATIONS:
INVARIANT_RELATION: SAME_INVARIANT | DIFFERENT_INVARIANT | UNCERTAIN
FAMILY_INVARIANT: value | none
REPAIR_CLOSURE_CONDITION: value | none
```

`RUN_LOCAL-WI-*` and `plan=` are not durable identities and must not be cited as
case ownership, claim authority, repair admission, or target family ids.

## Target-native handoff

For each work item:

### Existing durable case and finding

When exact `change=`, `case=`, and `finding=` are grounded, emit:

```text
/opsx-bug-disposition change=<CHANGE_ID> case=<CASE_ID> finding=<FINDING_ID> plan=<correlation-label> work_item=<RUN_LOCAL-WI-N>
```

The downstream command only resolves the target route; it does not persist a
SOURCE disposition receipt.

### Finding not yet materialized

Emit one unexecuted intake command per finding:

```text
/opsx-materialize-bug-queue mode=intake RAW_REPORT="<grounded finding text>"
```

Then use the returned exact `CASE_ID` and `INTAKE_ID` with the target
adjudication flow. Do not invent a case id, finding id, claim token, or direct
bug-team admission.

Grouping does not merge durable ownership. Even a pre-repair `BUG_FAMILY`
keeps one target case-map route per member unless an existing target case
already proves shared ownership.

## Output

```text
STATUS: FAMILY_PLAN_READY | ALL_ATOMIC | BLOCKED_NEEDS_CLEAN_INPUT | BLOCKED_NEEDS_INPUT | GO_UNSUPPORTED
MODE: READ_ONLY
COMMAND: /opsx-bug-family-plan
SOURCE_PARITY: NON_DURABLE_PLANNING_SUBSET
GO_SUPPORTED: false

FAMILY_PLAN_RECEIPT:
- change:
- case:
- plan_label:
- source_findings_loaded:
- families_emitted:
- atomic_emitted:
- members_preserved: true|false
- persistence: NONE

FAMILY_PLAN:
### WORK_ITEM: RUN_LOCAL-WI-...
- WORK_ITEM_TYPE:
- SOURCE_FINDING_IDS:
- FAMILY_KIND: PRE_REPAIR_ANALYSIS_ONLY
- INVARIANT_RELATION:
- EVIDENCE:

MEMBERSHIP_TRACE:
- <source finding> -> <run-local work item>

TARGET_ROUTE_HANDOFFS:
- exact /opsx-bug-disposition line | exact materialize intake line

WHAT_HAPPENED:
WHY:
NEXT_ACTION:
DO_NOT_DO_YET:
  - do not treat this plan as durable case or family authority
  - do not invoke repair without target materialize, claim, and classify gates
```

## Pipeline position

```text
ENTRY_STATE: clean product finding set
COMMAND_ACTION: non-durable family-vs-atomic analysis
EXIT_STATE: bounded work-item hints + target-native handoffs
NEXT_ALLOWED_STEP: /opsx-bug-disposition | /opsx-materialize-bug-queue
FAILURE_OR_RECOVERY: /opsx-bug-intake-clear
```

## Self-check

- [ ] Every source finding appears exactly once.
- [ ] No target family artifact was read as pre-repair membership authority.
- [ ] No durable file was written.
- [ ] Handoffs preserve one target case route per member.
- [ ] No claim token or target id was invented.
