---
component_id: opsx.commands.bug_disposition
component_version: 0.1.1
ownership: TARGET_ONLY
runtime_class: ACTIVE_WITH_LIMITS
source_commit: 91311ada29721cfb9aab7a00e1dc4d2a0a8d1982
source_path: .cursor/commands/opsx-bug-disposition.md
required_adapters: ["opsx-lite", "project-generic"]
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
contract_id: OPSX-WB-BUG-DISPOSITION-01
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

# /opsx-bug-disposition

Resolve one exact durable target finding to its next authoritative bug route
relative to a named OpenSpec Change. This is a read-only routing adaptation,
not a second current-Change adjudicator: `/opsx-classify-bug`, the case ledger,
and `/opsx-bug-team` retain target ownership.

## Authority and safety

```text
IMPLEMENTATION_STATUS: IMPLEMENTED_WITH_LIMITS
RUNTIME_STATUS: ACTIVE_WITH_LIMITS
CONTRACT_ID: OPSX-WB-BUG-DISPOSITION-01
MODE: READ_ONLY
READ_ONLY: true
SOURCE_PARITY: ROUTING_ONLY
CURRENT_CHANGE_DISPOSITION: NOT_ADJUDICATED_HERE
GO_SUPPORTED: false
CASE_LEDGER_WRITER: /opsx-materialize-bug-queue
CLASSIFICATION_OWNER: /opsx-classify-bug
REPAIR_OWNER: /opsx-bug-team
COMMIT_ALLOWED: NO
PRODUCTION_WRITES_ALLOWED: NO
```

The SOURCE command can persist standalone disposition receipts. That write
function is intentionally unavailable because target has no accepted
`dispositions/**` schema and already stores route truth in the case ledger and
append-only route trace.

- Do not create `opsx/runs/bug-cases/dispositions/**` or
  `family-plans/**`.
- Do not edit case files, route traces, OpenSpec, product code, tests, or
  registries.
- Do not label a finding `MUST_FIX_CURRENT_CHANGE`, `DEFER_PRODUCT_BUG`,
  `TECH_DEBT_ONLY`, `FALSE_POSITIVE`, or a security disposition here.
- Do not claim, classify, fix, test, materialize, or dispatch bug-team.
- Do not infer a route from Bugbot severity or a run-local family plan.

## User input

```text
$ARGUMENTS
```

SOURCE-compatible correlation tokens are accepted:

```text
change=<CHANGE_ID>
plan=<PLAN_ID>
work_item=<WORK_ITEM_ID>
GO
```

Target route resolution additionally requires:

```text
case=<CASE_ID>
finding=<FINDING_ID>
```

`plan=` and `work_item=` are audit labels only. They never override durable
case state.

Missing `change`, `case`, or `finding` returns `BLOCKED_NEEDS_INPUT` with an
actionable materialize/family-plan route. `GO` returns `GO_UNSUPPORTED` before
any mutation.

### Mandatory argument gate — FIRST ACTION

Before any tool call, repo read, OpenSpec lookup, case lookup, or search:

1. Parse only the explicit `$ARGUMENTS` tokens.
2. If `GO` is present, emit `GO_UNSUPPORTED` and STOP without tool calls.
3. If any of `change=`, `case=`, or `finding=` is missing or empty, emit
   `BLOCKED_NEEDS_INPUT` and STOP without tool calls.
4. Only after all three exact identifiers are present may source resolution
   begin.

This gate is mandatory for live activation and recovery runs. Missing input is
not permission to discover or infer identifiers from repository state.

## Exact source resolution

Read only:

```text
opsx/runs/bug-cases/<CASE_ID>/CASE.md
opsx/runs/bug-cases/<CASE_ID>/LEDGER.md
opsx/runs/bug-cases/<CASE_ID>/findings/<FINDING_ID>.md
```

Read the exact `ROUTE_TRACE.jsonl` only when `LEDGER.md` references it and the
latest event is needed to validate `ROUTE_STATE`. Never search all cases for a
matching finding.

Resolve the named Change with:

```text
openspec status --change "<CHANGE_ID>" --json
```

Use only status JSON `changeRoot` and `artifactPaths`. This command may report
whether the Change resolves, but must not perform SOURCE semantic disposition
from planning prose.

If the case or finding is absent, return `BLOCKED_TARGET_CASE_REQUIRED`; do not
invent ids or reconstruct distant chat context.

## Route matrix

Durable target state decides the next command:

| Durable evidence | Read-only route |
|---|---|
| `STATUS=available` and `CHAT_REQUIRED=true` | `/opsx-claim-next-bug case=<CASE_ID> chat_label="<label>"` |
| `STATUS=claimed` and durable `CLAIM_TOKEN` empty | exact `/opsx-materialize-bug-queue mode=update ... repair=mint_missing_claim_token`; use `release_corrupt_claim` when owner is absent |
| `STATUS=claimed` with token and classification absent | `/opsx-classify-bug case=<CASE_ID> finding=<FINDING_ID>` with token retained in context |
| `ROUTE_STATE` has an authoritative next action | echo that exact allowed command |
| terminal target status | `STOP — FINDING_TERMINAL` |
| backlog/blocked without an authoritative next action | `BLOCKED_NEEDS_TARGET_ROUTE` |
| route trace and ledger disagree | `BLOCKED_ROUTE_MISMATCH` |

Never route `available` directly to fix-bug. Never invent or echo an empty
claim token as usable. Never turn a backlog row into a claimable row.

Allowed authoritative next-command families are:

```text
/opsx-claim-next-bug
/opsx-classify-bug
/opsx-materialize-bug-queue
/opsx-update
/opsx-test
/opsx-fix-bug
/opsx-bug-team
/opsx-learn-from-bug
/opsx-admin-command-fix-command-defects
STOP
```

If the durable next action falls outside this list, return
`BLOCKED_NEEDS_TARGET_ROUTE` instead of forwarding it.

## What this command can conclude

It may conclude:

```text
ROUTE_DISPOSITION:
  CLAIM_REQUIRED
  | CLAIM_REPAIR_REQUIRED
  | CLASSIFICATION_REQUIRED
  | TARGET_ROUTE_READY
  | FINDING_TERMINAL
  | TARGET_ROUTE_BLOCKED
```

It may not conclude SOURCE semantic release classes. Therefore:

```text
CURRENT_CHANGE_DISPOSITION: NOT_ADJUDICATED_HERE
CHANGE_CLEAR: NOT_EVALUATED
RELEASE_CLEAR: NOT_EVALUATED
VERIFY_STATUS: NOT_EVALUATED
```

This limitation is functional rather than inert: the command grounds one exact
next action in the target durable route and prevents bypass of claim,
classification, and route-reconciliation gates.

## Output

```text
STATUS: ROUTE_READY | FINDING_TERMINAL | BLOCKED_NEEDS_INPUT | BLOCKED_TARGET_CASE_REQUIRED | BLOCKED_NEEDS_TARGET_ROUTE | BLOCKED_ROUTE_MISMATCH | GO_UNSUPPORTED
MODE: READ_ONLY
COMMAND: /opsx-bug-disposition
SOURCE_PARITY: ROUTING_ONLY
GO_SUPPORTED: false

ROUTE_RECEIPT:
- change:
- change_root:
- case:
- finding:
- plan_label:
- work_item_label:
- durable_status:
- chat_required:
- claim_token_present: yes|no
- route_state:
- grounding_cited:

ROUTE_DISPOSITION:
CURRENT_CHANGE_DISPOSITION: NOT_ADJUDICATED_HERE
CHANGE_CLEAR: NOT_EVALUATED
RELEASE_CLEAR: NOT_EVALUATED
VERIFY_STATUS: NOT_EVALUATED

NEXT_ACTION: one exact target command | STOP
WHY:
DO_NOT_DO_YET:
  - do not treat this receipt as semantic release disposition
  - do not bypass claim, classify, RED, or Owner GO gates
  - do not claim verify PASS
```

## Pipeline position

```text
ENTRY_STATE: named Change + exact durable target finding
COMMAND_ACTION: validate and expose the next authoritative target route
EXIT_STATE: route_ready | terminal | blocked
NEXT_ALLOWED_STEP: exactly NEXT_ACTION
FAILURE_OR_RECOVERY: materialize exact case evidence, then rerun read-only
```

## Self-check

- [ ] Exact case and finding paths only.
- [ ] No semantic SOURCE disposition was invented.
- [ ] No durable file changed.
- [ ] Claim-first and classify-first gates were preserved.
- [ ] `GO` remained unsupported.
