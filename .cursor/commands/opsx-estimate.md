---
component_id: opsx.commands.estimate
component_version: 0.4.0
ownership: TARGET_ONLY
runtime_class: ACTIVE
source_commit: 91311ada29721cfb9aab7a00e1dc4d2a0a8d1982
source_path: .cursor/commands/opsx-estimate.md
required_adapters: ["opsx-lite", "project-generic"]
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
contract_id: OPSX-WB-ESTIMATE-01
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

# /opsx-estimate

Record a facts-first delivery estimate ledger. The minimal estimate unit is one
capability spec; a change is only the container for its specs and tasks.

## User input

```text
$ARGUMENTS
```

Supported forms:

```text
/opsx-estimate change=<change-id> MODE=FORECAST
/opsx-estimate change=<change-id> MODE=ACTUALS
```

Optional Owner evidence:

```text
COMPLEXITY_BAND=S|M|L|XL
SPEC=<capability-id>
PACE_NOTE=<phrase>
WALL_CLOCK_GUESS=<duration|unknown>
WALL_CLOCK_ACTUAL=<duration|unknown>
```

`SPEC=` scopes a supplied band or note to one capability. Without `SPEC=`, the
evidence applies to the change rollup and to each spec only when the Owner
clearly said it applies to all specs.

## Authority and safety

```text
IMPLEMENTATION_STATUS: IMPLEMENTED
RUNTIME_STATUS: ACTIVE
CONTRACT_ID: OPSX-WB-ESTIMATE-01
GREEN_WRITER: NO
ARCHIVE_VERDICT_WRITER: NO
COMMIT_ALLOWED: NO
VERIFY_AUTHORITY: NO
PRODUCTION_WRITES_ALLOWED: NO
```

- This command is not `/opsx-verify` or `/opsx-archive` and cannot emit their
  `GO`, `NO_GO`, Product Acceptance, completion, or readiness verdicts.
- Do not archive, sync specs, promote decisions, edit tasks, change product
  code, run WB/HTTP/PG/CH/Redis writes, access secrets, deploy, commit, merge,
  push, rebase, checkout, reset, clean, or stash.
- Do not install dependencies or modify `pyproject.toml` / `poetry.lock`.
- No project tests are needed to record an estimate. If a separately justified
  Python check becomes necessary, follow the target Poetry policy and use
  `poetry run ...`; never run a bare project test/typecheck command.
- Follow `.cursor/commands/opsx-execution-preflight.md`: exact paths first,
  bounded reads, and no whole-repository or whole-archive content dump.

## Projectization gate

Read `opsx/projectization.yaml` before writing. Unless `status: COMPLETE`,
`normal_commands_enabled: true`, and adapters include both `opsx-lite` and
`poetry-python`, stop:

```text
STATUS: FAIL_CLOSED
REASON: OPSX_PROJECTIZATION_REQUIRED
NEXT_ACTION: /opsx-projectize
```

## Target path authority

1. Preserve OpenSpec store selection semantics. If the Owner names a registered
   store, resolve it with `openspec store list --json` and pass `--store <id>`
   to OpenSpec commands. Otherwise use the nearest local OpenSpec root.
2. For an active change, run
   `openspec status --change "<change-id>" --json` and use its `planningHome`,
   `changeRoot`, and `artifactPaths`; never reconstruct an active root when
   status supplies it.
3. The only target archive root is
   `<planningHome.changesDir>/archive`. In this repository's local planning
   home that is `openspec/changes/archive`.
4. If active status does not resolve the requested change, inspect only exact
   archive candidates
   `<planningHome.changesDir>/archive/*-<change-id>`. Use an Owner-supplied
   dated path when present; otherwise require a unique candidate or choose the
   newest date-prefixed candidate and report that choice. No candidate means
   `BLOCKED_MISSING_CHANGE`.
5. Never use or create a second archive root. Never move a change.

The estimate directory stays below the resolved active or archived
`changeRoot`, so it moves with the change when `/opsx-archive` later performs
the lifecycle move.

## Spec discovery

Build the minimal unit list from bounded change evidence:

1. capability ids represented by `artifactPaths.specs.existingOutputPaths`, or
   directories under `<changeRoot>/specs/<capability>/`;
2. when no delta spec exists, capability ids explicitly declared by
   `proposal.md` or `design.md` and backed by an exact
   `openspec/specs/<capability>/spec.md`.

Do not inventory all main specs to guess a capability. Every discovered id gets
one row. An empty list is `BLOCKED_MISSING_SPECS` and writes nothing.

## Band resolution

Resolve `COMPLEXITY_BAND` separately for each capability and record
`BAND_SOURCE`:

1. `flag`: an explicit valid flag scoped to the capability or clearly to all;
2. `prose`: unambiguous Owner wording such as `S`, `маленький`, or an equally
   clear size statement;
3. `unknown`: no unambiguous evidence.

An unknown band does not fail the write and is not `PRIOR_EMPTY`. Record
`unknown`, finish the ledger, and ask once for `S|M|L|XL`.

## Wall-clock evidence

Prefer an explicit Owner guess/actual. Otherwise use bounded path history:

1. `START_COMMIT`: earliest commit that first lands durable work under the
   active change path for this change id.
2. `END_COMMIT`: commit that moves the change to the resolved target archive
   candidate under `<planningHome.changesDir>/archive`.
3. With both anchors, wall clock is committer-date `END - START`; record both
   SHAs and dates and set `WALL_CLOCK_SOURCE: git-span`.
4. With either anchor missing, record `unknown`; do not substitute a short
   apply/product commit cluster or chat duration.

Run only path-scoped, time-bounded git history queries needed for these anchors.
A lifecycle git span is a fact slice, not a velocity claim.

## Closed-spec prior

A closed spec is a capability row in `estimate/ACTUALS.md` below an archived
change. An archive directory alone, open commits, WORKLOG, or another analysis
does not create a closed spec.

Search only the target archive root and only exact
`*/estimate/ACTUALS.md` candidates. Read at most ten newest candidate files
unless the Owner explicitly expands the bound, and look only for overlapping
capability ids.

- At least one overlapping closed row:
  `PRIOR_STATUS: PRIOR_HINT`, `LEARNING_STATUS: HINT_ONLY`.
- No ACTUALS files exist, or all existing candidate files fit inside the bound
  and none has an overlapping row:
  `PRIOR_STATUS: PRIOR_EMPTY`,
  `LEARNING_STATUS: LEARNING_UNAVAILABLE`.
- More candidate files exist than were examined and no overlap was found:
  both statuses are `unknown`; do not claim `PRIOR_EMPTY`.

Prior hints are descriptive and weak. Do not derive a numeric velocity,
probability, or promised delivery date.

## Modes and write boundary

| Mode | Sole write | Preconditions |
|---|---|---|
| `MODE=FORECAST` | `<changeRoot>/estimate/FORECAST.md` | resolved change and at least one spec |
| `MODE=ACTUALS` | `<changeRoot>/estimate/ACTUALS.md` | resolved change, specs, and existing FORECAST |

If mode is absent or invalid, return `BLOCKED_NEEDS_MODE`. ACTUALS may be
recorded for an active change only when Owner actual evidence exists, but its
rows do not become closed-spec prior until the change is archived.

```text
WRITE_BOUNDARY:
- <changeRoot>/estimate/FORECAST.md
- <changeRoot>/estimate/ACTUALS.md
```

Create only the needed `estimate` directory and one selected mode file. Refuse
any resolved write path outside `changeRoot`.

## FORECAST artifact

Write:

```markdown
# ESTIMATE FORECAST

CONTRACT_ID: OPSX-WB-ESTIMATE-01
MODE: FORECAST
CHANGE_ID: <id>
RECORDED_AT_UTC: <timestamp>
STATUS: FORECAST_RECORDED
UNIT: SPEC
PRIOR_STATUS: PRIOR_EMPTY|PRIOR_HINT|unknown
LEARNING_STATUS: LEARNING_UNAVAILABLE|HINT_ONLY|unknown
WALL_CLOCK_SOURCE: Owner|git-span|unknown

| Capability | COMPLEXITY_BAND | BAND_SOURCE | WALL_CLOCK_GUESS | PACE_NOTE | Prior hint |
|---|---|---|---|---|---|
| <id> | S|M|L|XL|unknown | flag|prose|unknown | <value> | <value> | none|WEAK |

TASKS_TOTAL: <count|unknown>
TASKS_DONE: <count|unknown>
TASKS_OPEN: <count|unknown>
HAS_DESIGN: yes|no
SPEC_COUNT: <count>
START_COMMIT: <sha/date|unknown>
END_COMMIT: <sha/date|unknown>
NOT_VERIFY_GO: true
NOT_ARCHIVE_AUTHORITY: true
NO_INVENTED_PRIOR: true
COMMIT_SPAN_NOT_FULL_SESSION: true|false|n/a
```

## ACTUALS artifact

Require and read the matching `estimate/FORECAST.md`; otherwise return
`BLOCKED_MISSING_FORECAST` without writing.

```markdown
# ESTIMATE ACTUALS

CONTRACT_ID: OPSX-WB-ESTIMATE-01
MODE: ACTUALS
CHANGE_ID: <id>
RECORDED_AT_UTC: <timestamp>
STATUS: ACTUALS_RECORDED
UNIT: SPEC
FORECAST_PATH: estimate/FORECAST.md
CLOSES_SPECS: yes|no

| Capability | Forecast band | Actual felt | BAND_SOURCE | Wall clock F/A | Match |
|---|---|---|---|---|---|
| <id> | <value> | S|M|L|XL|unknown | flag|prose|unknown | <value> | yes|no|unknown |

MATCHED: <per-spec facts|none>
MISSED: <per-spec facts and WHY_FACT|none>
UNKNOWN: <per-spec facts|none>
WALL_CLOCK_ACTUAL: <value|unknown>
WALL_CLOCK_SOURCE: Owner|git-span|unknown
ARCHIVE_PATH: <resolved path|active_not_archived>
START_COMMIT: <sha/date|unknown>
END_COMMIT: <sha/date|unknown>
NOT_VERIFY_GO: true
NOT_ARCHIVE_AUTHORITY: true
COMMIT_SPAN_NOT_FULL_SESSION: true|false|n/a
```

Set `CLOSES_SPECS: yes` only when `changeRoot` is below the resolved target
archive root. Unknown actuals remain honest unknowns; do not turn them into
matched or missed claims.

## Procedure

1. Apply the projectization gate and parse `change=` plus `MODE=`.
2. Resolve active status or the target archive sibling without scanning other
   repository areas.
3. Discover capability specs; stop on an empty list.
4. FORECAST only: inspect the bounded closed-spec corpus and classify prior.
5. Resolve per-spec bands and wall-clock evidence.
6. Validate the exact destination against `changeRoot`.
7. Write exactly one mode artifact.
8. Return the receipt and stop; do not invoke verify, archive, apply, commit,
   production, or deployment commands.

## Status and output

Status tokens:

```text
FORECAST_RECORDED
ACTUALS_RECORDED
BLOCKED_MISSING_CHANGE
BLOCKED_MISSING_SPECS
BLOCKED_MISSING_FORECAST
BLOCKED_NEEDS_MODE
BLOCKED_NEEDS_EVIDENCE
BLOCKED_PATH_AUTHORITY
PRIOR_EMPTY
LEARNING_UNAVAILABLE
FAIL_CLOSED
```

Lead with 2–4 human-readable Russian lines: what was recorded, band source,
clock source/anchors, and whether a closed-spec prior exists. Then emit:

```text
STATUS:
MODE:
CHANGE_ID:
CHANGE_ROOT:
ARCHIVE_ROOT: <planningHome.changesDir>/archive
SPEC_COUNT:
PRIOR_STATUS:
LEARNING_STATUS:
BAND_SOURCE:
WALL_CLOCK_SOURCE:
EVIDENCE_PATH:
WHAT_HAPPENED:
WHY:
CURRENT_STATE:
OWNER_DECISION_REQUIRED:
NEXT_ACTION:
ALSO_OK:
DO_NOT_DO_YET:
```

For unknown bands, `ALSO_OK` asks once for the missing letter without implying
the ledger failed. After archived ACTUALS, explain that recorded capability
rows can supply only weak future hints. Never claim functional proof, verify
success, archive readiness, or execution of a production path.
