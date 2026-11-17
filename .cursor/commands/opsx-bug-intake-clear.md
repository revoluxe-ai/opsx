---
component_id: opsx.commands.bug_intake_clear
component_version: 0.1.0
ownership: TARGET_ONLY
runtime_class: ACTIVE_WITH_LIMITS
source_commit: 91311ada29721cfb9aab7a00e1dc4d2a0a8d1982
source_path: .cursor/commands/opsx-bug-intake-clear.md
required_adapters: ["opsx-lite", "project-generic"]
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
contract_id: OPSX-WB-BUG-INTAKE-CLEAR-01
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


# /opsx-bug-intake-clear

Normalize a bounded raw finding set, remove current-set duplicates, reconcile
against one explicitly named durable target case when available, and emit the
next target-native bug route. This adaptation is a real read-only subset of the
SOURCE interface. It does not implement SOURCE suppression or receipt writes.

## Authority and safety

```text
IMPLEMENTATION_STATUS: IMPLEMENTED_WITH_LIMITS
RUNTIME_STATUS: ACTIVE_WITH_LIMITS
CONTRACT_ID: OPSX-WB-BUG-INTAKE-CLEAR-01
MODE: READ_ONLY
READ_ONLY: true
SOURCE_PARITY: READ_ONLY_SUBSET
CASE_LEDGER_WRITER: /opsx-materialize-bug-queue
GO_SUPPORTED: false
COMMIT_ALLOWED: NO
PRODUCTION_WRITES_ALLOWED: NO
```

- Existing `opsx/runs/bug-cases/**` schemas and target commands win over
  SOURCE assumptions.
- Do not create or edit `INDEX.md`, `LEDGER.md`, findings, intakes, results,
  suppression registries, family artifacts, OpenSpec, product code, or tests.
- Do not classify bug truth, claim work, fix code, run product tests, access
  production, or call network services.
- `/opsx-materialize-bug-queue` remains the only command in this workflow that
  may materialize or update the durable case map.
- Follow `.cursor/commands/opsx-source-resolution.md` and
  `.cursor/commands/opsx-execution-preflight.md`: exact paths first and bounded
  reads only.

## User input

```text
$ARGUMENTS
```

Supported SOURCE-compatible tokens:

```text
case=<CASE_ID>
finding=<FINDING_ID>
focus=all|open_intake|hunt_dupes|terminal_only|first_intake
limit=<N>
GO
```

Raw findings must be in the same message or the immediately preceding complete
producer output as `HUNT_FINDINGS:`, `FINDINGS:`, or a Bugbot table. Unknown
tokens or no parseable findings produce `BLOCKED_NEEDS_INPUT`.

`GO` never authorizes a write in this adaptation. Return `GO_UNSUPPORTED` and
the same read-only plan.

## Target source resolution

1. Parse at most `limit` rows, default 40.
2. If `case=` is absent, do not scan all case directories. Set
   `HISTORICAL_CASE_MAP: NOT_REQUESTED`.
3. If `case=` is present, read only:
   - `opsx/runs/bug-cases/<CASE_ID>/CASE.md`;
   - `opsx/runs/bug-cases/<CASE_ID>/LEDGER.md`;
   - the exact selected intake/finding packet when its id is known;
   - an exact result path referenced by those records when needed.
4. `INDEX.md` may be read only for the exact named case recovery row when the
   case-local record is insufficient. Never infer a case from repository-wide
   similarity.
5. Missing canonical case files are `EVIDENCE_GAP`; they are not permission to
   invent or materialize a case.

## Normalization and current-set dedupe

For every source row preserve:

```text
SOURCE_ID
LOC_LINK
LITERAL_CLAIM
ROOT_FINGERPRINT
SEVERITY
PROVENANCE
```

Normalize path separators to `/` for comparison. Dedupe first by non-empty
`ROOT_FINGERPRINT`; otherwise by normalized `LOC_LINK` plus normalized
`LITERAL_CLAIM`. A producer id alone is never identity evidence. Echoes in a
handoff block do not become additional findings.

Assign run-local ids `CLR-<short-slug>-NNN`. They are provenance labels, not
durable target `FINDING_ID` values.

Classify repair authority before product routing:

```text
PROCESS_CONTRACT:
  .cursor/** | opsx/** | .agents/**

PRODUCT:
  every other grounded repository-relative path
```

`PROCESS_CONTRACT` rows route to
`/opsx-admin-command-fix-command-defects` and never enter product
`CLEAN_FINDINGS`.

## Historical reconciliation

Historical matching is allowed only inside the exact named case. Use durable
fields from the target case map:

- terminal duplicate: `STATUS=closed_fixed|closed_false_positive|
  duplicate_root_observation|absorbed_by_parent_fix|closed_duplicate`, with
  matching fingerprint or loc+claim evidence;
- active exact duplicate: a matching non-terminal finding already owned by the
  case;
- ambiguous identity: insufficient corroboration.

Do not treat `backlog`, `blocked`, `available`, `claimed`, `adjudicating`,
`ready_for_fix`, `implementing`, or `verification` as terminal. Do not suppress
anything from chat prose alone.

When an exact newer intake duplicates a canonical finding, emit but do not run:

```text
/opsx-materialize-bug-queue mode=update case=<CASE_ID> intake=<INTAKE_ID> finding=<CANONICAL_FINDING_ID> relation=duplicate_intake canonical_intake=<CANONICAL_INTAKE_ID> related_finding=<CANONICAL_FINDING_ID>
```

This line delegates all durable updates to the existing writer and must include
only ids proven by the named case.

## Routing

```text
PROCESS rows
  → /opsx-admin-command-fix-command-defects

zero product CLEAN_FINDINGS
  → STOP — NOTHING_TO_REPAIR

one or more product CLEAN_FINDINGS
  → /opsx-bug-family-plan change=<CHANGE_ID>
```

Do not route a raw or clean row directly to `/opsx-fix-bug` or
`/opsx-bug-team`. Family planning is non-durable in this target adaptation and
the later target case-map route remains authoritative.

## Output

```text
STATUS: CLEAR_COMPLETE | CLEAR_PLAN | BLOCKED_NEEDS_INPUT | EVIDENCE_GAP | GO_UNSUPPORTED
MODE: READ_ONLY
COMMAND: /opsx-bug-intake-clear
SOURCE_PARITY: READ_ONLY_SUBSET
GO_SUPPORTED: false

SOURCE_MODE:
SOURCE_FINDINGS_COUNT:
HISTORICAL_CASE_MAP: PRESENT | ABSENT | NOT_REQUESTED
CASE_PATH: exact path | none

CLEAN_FINDINGS_COUNT:
CLEAN_FINDINGS:
  - FINDING_ID: CLR-...
    SOURCE_ID:
    REPAIR_AUTHORITY: PRODUCT
    LOC_LINK:
    LITERAL_CLAIM:
    ROOT_FINGERPRINT:
    PROVENANCE:
    RECONCILIATION_CLASS: NEW_DISTINCT | RELATED_DISTINCT_RESIDUAL

PROCESS_FINDINGS_ROUTED:
ACTIVE_EXACT_DUPLICATES:
TERMINAL_DUPLICATES:
AMBIGUOUS_ITEMS:
MATERIALIZE_BATCH: exact delegated lines | none

WHAT_HAPPENED:
WHY:
NEXT_ACTION:
DO_NOT_DO_YET:
  - do not claim durable cleanup was written
  - do not invoke product repair before target case routing
```

## Pipeline position

```text
ENTRY_STATE: bounded raw findings
COMMAND_ACTION: normalize + current-set dedupe + optional exact-case reconcile
EXIT_STATE: clean read-only set + delegated target routes
NEXT_ALLOWED_STEP: /opsx-bug-family-plan | target process repair | STOP
FAILURE_OR_RECOVERY: paste grounded findings or provide exact case evidence
```

## Self-check

- [ ] No file was changed.
- [ ] No global bug-case scan was used.
- [ ] Producer ids were not treated as durable identity.
- [ ] Process findings were excluded from product routing.
- [ ] Every durable mutation is an unexecuted
      `/opsx-materialize-bug-queue` handoff.
