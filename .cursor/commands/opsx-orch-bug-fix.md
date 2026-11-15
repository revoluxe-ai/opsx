---
description: "Full GAME orch-bug-fix semantics with STROY target runtime binding"
argument-hint: "[change=<id>] [mission=<id>] [findings=...] [GO]"
stroy_transfer_subject: opsx-orch-bug-fix-full-transplant-v1
source_semantics_commit: 65b63a8dccd82d72e377d796ec02905dd7a50719
source_repo: revoluxe-ai/tiriel-game
source_branch: stage
source_contract_id: OPSX-GAME-ORCH-BUG-FIX-01
target_runtime_contract_id: OPSX-TARGET-ORCH-RUNTIME-01
---

# STROY-SYSTEM TARGET OVERRIDE — HIGHEST PRIORITY

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


### STROY runtime binding (`OPSX-TARGET-ORCH-RUNTIME-01`)

```text
SOURCE_SEMANTICS_PIN: OPSX-GAME-ORCH-BUG-FIX-01 @ 65b63a8dccd82d72e377d796ec02905dd7a50719
OUTER_ASYNC_TRANSPORT: tools/temporal (existing parent workflow only)
NESTED_TEMPORAL_START: NO
IN_COMMAND_TASK_BACKGROUND: FORBIDDEN (map GAME TASK_BACKGROUND semantics to parent harvest/poll)
SYNC_PLAN_HELPER: python tools/temporal/orch_target.py bug-fix-plan
SYNC_ADMISSION_HELPER: .cursor/pipeline/orch-bug-fix/orch_bug_fix.py
ADDENDUM: .cursor/pipeline/orch-core/ASYNC_TRANSPORT_STROY_TARGET.md
WRAPPER_PRODUCT_WRITE: NO
GIT_COMMIT: FORBIDDEN
GIT_PUSH: FORBIDDEN
OWNER_WAKE_ON_DETERMINISTIC_TRANSITION: NO
```

#### STROY persist mapping (execution authority over donor GO paths)

When the GAME body says `DELEGATED_PERSIST` for family-plan / disposition / materialize,
the sync admission helper (`.cursor/pipeline/orch-bug-fix/orch_bug_fix.py`) executes
**native AUTO_PERSIST** via `.cursor/pipeline/bug_lifecycle/**` (no READ_ONLY host
delegation, no Owner GO). Authorized durable writer remains
`/opsx-materialize-bug-queue` (native `materialize_bug_queue.py`). Family-plan SSCA is
`opsx/runs/orch-missions/<MISSION_ID>/family-plan.json`. Disposition SSCA is case
ledger + `opsx/runs/bug-cases/materialize/disposition-*.json` receipts.
`BUG_BATCH_CLEAR` requires materialize close receipts bound to `execution_id` + `head`.
Hermetic acceptance: `native_mission=YES` on the admission helper request.
`NESTED_TEMPORAL_START` stays `NO`. `OWNER_WAKE_ON_DETERMINISTIC_TRANSITION=NO`.

The sections below preserve the full GAME `/opsx-orch-bug-fix` PP DAG, orch-core cross-refs,
and regression fixture contract. STROY bindings above are the only execution deltas.

---
component_id: opsx.commands.orch_bug_fix
component_version: 0.1.0
ownership: PROJECT_LOCAL
runtime_class: ACTIVE
source_commit: local
source_path: .cursor/commands/opsx-orch-bug-fix.md
contract_id: OPSX-GAME-ORCH-BUG-FIX-01
required_adapters: ["opsx-lite", "project-generic"]
local_modification_note: "Experimental bug-fix orchestration scenario — consumes OPSX-ORCH-CORE-01 + OPSX-ORCH-ASYNC-TRANSPORT-01; parallel to /opsx-supervisor, does not replace it"
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
newline_normalization: none
---

# TARGET OVERRIDE — HIGHEST PRIORITY

**Contract:** `OPSX-GAME-ORCH-BUG-FIX-01`  
**Orchestration core:** `OPSX-ORCH-CORE-01` (`.cursor/pipeline/orch-core/CORE.md`)  
**Async transport:** `OPSX-ORCH-ASYNC-TRANSPORT-01` (`.cursor/pipeline/orch-core/ASYNC_TRANSPORT.md`)  
**Runtime class:** `ACTIVE`

### Target authority paths

- Orchestration core: `.cursor/pipeline/orch-core/**`
- Mission state: `opsx/runs/orch-missions/**`
- Bug PP artifacts: `opsx/runs/bug-cases/**` (read/consume; writers remain upstream commands)
- Changes: `openspec/changes/**` (read for disposition context)
- Adapter: `opsx/adapters/opsx-lite/README.md` (TARGET; GAME donor `vue-npm` is non-authoritative)
- Quality gates: `opsx/project/quality-gates.md`

### Hard target bans

- Do not modify `/opsx-supervisor` or migrate users to this command automatically.
- Do not install dependencies / modify `package.json` or lockfile.
- Do not push, open PR, rebase, force, deploy, or commit.
- Do not access production / Kubernetes / secrets.
- Mandatory declared executable check: resolve `product.build` via `opsx/project/capabilities.yaml` when product code is touched by dispatched workers (not by this command alone).
- Do not model `/opsx-bug-team` WAVE_1–WAVE_4 as outer stages.
- Do not automatically follow `/opsx-project-bug-hunt` NEXT_ACTION from Bug Team in v1.

### Projectization gate

Read `opsx/projectization.yaml` before any write.
If `status` != `COMPLETE` or `normal_commands_enabled` is false:

```text
STATUS: FAIL_CLOSED
REASON: OPSX_PROJECTIZATION_REQUIRED
NEXT_COMMAND: /opsx-projectize
```

# /opsx-orch-bug-fix

**Experimental bug-fix orchestration scenario** for the `/opsx-orch-*` family.

Takes raw/current bug findings and autonomously drives the complete
bug-disposition / repair / ownership lifecycle for the **current batch** until a
truthful mission-scoped terminal state.

```text
ORCHESTRATOR CORE = HOW (OPSX-ORCH-CORE-01)
THIS COMMAND      = WHAT DAG / routing / gates (bug-fix PP)
EXECUTOR          = WHO (Task background primary; HOST_INLINE fallback)
```

Parallel experimental lane to `/opsx-supervisor` — **does not replace** it.

## What this is

* Scenario command consuming shared orch core + async transport contracts.
* Autonomous outer PP over existing bug commands (intake → family → disposition → route).
* One opaque `/opsx-bug-team` background Task per admitted MUST_FIX work item.
* Automatic routine persistence when no semantic Owner decision is required
  (`AUTO_PERSIST_DELEGATE=YES`).
* Mission-scoped `BUG_BATCH_CLEAR` without global project bug debt as gate.
* Post-repair durable close when Bug Team returns `READY_FOR_CLOSURE` but case not yet
  `closed_fixed`.
* Mechanical `EVOLUTION_QUEUE` built only from persisted disposition receipts.

## What this is NOT

* NOT `/opsx-supervisor` and not a migration target for Supervisor users.
* NOT Bug Team internal orchestration (classify / RED / GREEN / fanout / learning).
* NOT disposition authority (→ `/opsx-bug-disposition`).
* NOT durable bug lifecycle writer (→ `/opsx-materialize-bug-queue`).
* NOT Evolution debt writer (→ `/opsx-evolution`).
* NOT project-wide discovery loop (→ caller returns after batch clear).
* NOT Bugbot / project-bug-hunt discovery in v1 unless strictly required by current batch.

Follow `.cursor/commands/opsx-source-resolution.md` and
`.cursor/commands/opsx-execution-preflight.md`.

Regression fixtures: `.cursor/commands/_fixtures/orch-bug-fix/`.  
Contract tests: `tests/unit/opsxOrchBugFixContract.test.mjs`.

## User input

```text
$ARGUMENTS
```

| Token | Effect |
|-------|--------|
| `change=<CHANGE_ID>` | OpenSpec change under repair batch (required for disposition) |
| `mission=<MISSION_ID>` | Resume interrupted mission |
| `findings=` / inline `HUNT_FINDINGS:` / `CLEAN_FINDINGS:` | Raw finding set (same shapes as intake-clear) |
| `GO` | **MISSION_GO** only — Owner explicitly starts/resumes the whole mission (not routine upstream persist) |

Preferred entry: inline findings or immediate prior hunt/Bugbot producer message +
`change=`.

### Bare invocation source precedence (mandatory)

When `$ARGUMENTS` has **no** explicit `mission=` / `change=` / `findings=` / `GO`,
resolve the admitted batch using **only** this order:

```text
R0  explicit mission=<id>
      → resume exactly that mission (ignore immediate prior findings)

R1  explicit inline findings / HUNT_FINDINGS / CLEAN_FINDINGS in $ARGUMENTS
      → mint NEW mission; admit that finding set

R2  immediate previous supported Bugbot / hunt / review output with actionable findings
      → mint NEW mission from that finding set (do not require Owner re-paste)

R3  no fresh source from R0–R2
      → informational no-input only: may report latest terminal mission state
```

**Hard invariant:**

```text
TERMINAL_PREVIOUS_MISSION_MUST_NOT_SHADOW_FRESH_IMMEDIATE_FINDINGS=YES
```

Rules:

- A prior mission in `BUG_BATCH_CLEAR` (or any other terminal state) MUST NOT
  block R1/R2 admission.
- Bare `/opsx-orch-bug-fix` with a valid immediate prior finding set MUST
  start/admit that batch without manual `findings=` paste.
- **Immediate previous only** — the message directly before invocation. Do not
  fuzzy-search older chat history.
- Supported immediate prior shapes (non-exhaustive): Bugbot review table with
  actionable rows; `HUNT_FINDINGS:` / `CLEAN_FINDINGS:` blocks; intake-clear
  output carrying actionable findings for a new batch.
- Unsupported / ambiguous immediate prior → R3 (honest no-input), not silent
  resume of latest terminal mission.
- Resolve `change=` deterministically at admission when possible (single active
  change, branch slug, explicit prior context). If genuinely ambiguous, admit the
  finding batch and block only at the first stage that requires Change authority
  — do **not** discard the admitted findings.
- Process-contract / OPSX command-surface findings are **out of scope** for this
  command — route to `/opsx-meta-fix-command-defects` (do not run product bug
  lifecycle PP on `.cursor/commands/**` defects).

## Architecture decision references

| Concern | Authority |
|---------|-----------|
| Shared orchestration mechanics | `OPSX-ORCH-CORE-01` |
| Background Task transport | `OPSX-ORCH-ASYNC-TRANSPORT-01` |
| Intake normalization | `/opsx-bug-intake-clear` |
| Family planning | `/opsx-bug-family-plan` |
| Release disposition | `/opsx-bug-disposition` |
| Repair episode (opaque) | `/opsx-bug-team` |
| Durable close / defer / FP | `/opsx-materialize-bug-queue` |
| Structural debt | `/opsx-evolution` |
| Optional mission sanity | `/opsx-bug-debt-board` (non-global gate) |

## Responsibility map (mandatory)

### ORCH owns

- Intake sequencing
- Family-plan sequencing/persistence (READ_ONLY → GO when auto-delegable)
- Disposition sequencing/persistence (READ_ONLY → GO when auto-delegable)
- Outer routing by disposition class
- **One outer background Task per complete Bug Team episode**
- Post-Bug-Team durable close when still required
- FP / defer durable ownership dispatch
- Exact disposition `DEBT_TAIL` queue (no merge)
- Evolution handoff after all MUST_FIX durably closed
- Mission ownership audit
- Mission-scoped `BUG_BATCH_CLEAR`
- Pre-dispatch worktree ownership reconciliation (S3.5)
- Delegated upstream persist (`DELEGATED_PERSIST`) when `AUTO_PERSIST=REQUIRED`

### BUG TEAM owns (opaque to Orch)

- Repair episode internals: classify, RED, implementation, GREEN
- Same-invariant post-GREEN fanout, fan-in, repair learning
- `READY_FOR_CLOSURE` and any close it actually performs internally

### EVOLUTION owns

- Structural debt persistence, tail dedupe/merge/reconciliation

### MATERIALIZE-BUG-QUEUE owns

- `CLOSED_FIXED`, deferred backlog, false-positive terminal state

## Mission durable root

```text
MISSION_DURABLE_ROOT: opsx/runs/orch-missions/<MISSION_ID>/
  mission.json
  workers/<DISPATCH_ID>.json
  locks/product-writer.json
  evolution-queue.ndjson
  receipts/
    worktree-ownership-<WORK_ITEM_ID>.json
```

Schema: `.cursor/pipeline/orch-core/schemas/mission.schema.json`.

## AUTO_PERSIST delegation (mandatory)

Consumes `AUTO_PERSIST_DELEGATION` from `OPSX-ORCH-CORE-01`. Scenario-specific
stages and SSCA targets:

```text
AUTO_PERSIST_DELEGATE=YES
OWNER_GO_REQUIRED=NO when AUTO_PERSIST=REQUIRED

AUTO_PERSIST=REQUIRED when ALL:
  OWNER_DECISION_REQUIRED=none
  upstream semantic output valid
  no AUTHORITY_CONFLICT | NEEDS_ADJUDICATION | BLOCKED_NEEDS_EVIDENCE
  no unresolved MIXED_DISPOSITION routing split
  persistence is routine lifecycle materialization (plan / disposition / materialize close)

DELEGATED_PERSIST stages (same autonomous loop iteration as preceding READ_ONLY):
  S1b → /opsx-bug-family-plan GO          SSCA: family-plans/<PLAN_ID>.json exists
  S2b → /opsx-bug-disposition GO           SSCA: dispositions/<DISPOSITION_ID>.json exists
  S5b → /opsx-materialize-bug-queue …      SSCA: durable closed_fixed / defer / FP row

FORBIDDEN when AUTO_PERSIST=REQUIRED:
  NEXT_REAL_ACTION: Owner must send … GO
  MISSION_STATUS: ACTIVE — awaiting GO
  stopping at S1a/S2a/S5a without immediate delegated persist
```

`MISSION_GO` (Owner token) starts/resumes the **whole mission** only. It is **not**
upstream `GO_PERSIST_*` and must not be required between READ_ONLY and persist when
`AUTO_PERSIST=REQUIRED`.

## PP DAG (outer stages)

```text
S0  INTAKE_CLEAR + REPAIR_AUTHORITY_GATE
      /opsx-bug-intake-clear (Pass 0.25 mandatory)
      PROCESS_FINDINGS_ROUTED_COUNT > 0
        → emit PROCESS_HANDOFF to /opsx-meta-fix-command-defects
        → NEVER admit process rows into S1–S4 product PP
      Mixed batch: product CLEAN_FINDINGS continue S1+; process handoff parallel
      CLEAN_FINDINGS=0 AND PROCESS_FINDINGS_ROUTED only
        → PARTIAL_COMPLETE (process handoff; no product BUG_BATCH_CLEAR)
      CLEAN_FINDINGS=0 AND PROCESS_FINDINGS_ROUTED=0 → BATCH_CLEAR terminal

S1a FAMILY_PLAN_READ_ONLY
      /opsx-bug-family-plan READ_ONLY

S1b FAMILY_PLAN_AUTO_PERSIST
      when AUTO_PERSIST=REQUIRED → DELEGATED_PERSIST /opsx-bug-family-plan GO
      SSCA: family-plans/<PLAN_ID>.json exists

S2a DISPOSITION_READ_ONLY (per work item)
      /opsx-bug-disposition READ_ONLY

S2b DISPOSITION_AUTO_PERSIST (per work item)
      when AUTO_PERSIST=REQUIRED → DELEGATED_PERSIST /opsx-bug-disposition GO
      SSCA: dispositions/<DISPOSITION_ID>.json exists before repair

S3  ROUTE_BY_DISPOSITION
      deterministic matrix (below)

S3.5 WORKTREE_OWNERSHIP_RECONCILE (mandatory before first S4 dispatch per work item)
      map dirty product paths → WORK_ITEM_ID / MEMBER_ID (see below)
      SSCA: receipts/worktree-ownership-<WORK_ITEM_ID>.json for each MUST_FIX dispatch

S4  MUST_FIX_REPAIR (sequential, one work item at a time)
      acquire PRODUCT_WRITER
      → ONE TASK_BACKGROUND with ONE complete /opsx-bug-team:
         plan=<PLAN_ID> work_item=<WORK_ITEM_ID> disposition=<DISPOSITION_ID>
         [member_ids=<MUST_FIX subset>]
      → autonomous poll/harvest (no Owner wake)
      → confirmed EXECUTION_TERMINAL → release lock
      → consume top-level Bug Team semantic result only

S5a REPAIR_CLOSURE_EVAL (outer SSCA)
      closure owner matrix (below)

S5b MATERIALIZE_AUTO_PERSIST
      when READY_FOR_CLOSURE + no durable closed_fixed + AUTO_PERSIST=REQUIRED
      → DELEGATED_PERSIST /opsx-materialize-bug-queue mode=update … close=fixed
      SSCA: durable closed_fixed exists

S6  NON_REPAIR_ROUTES
      DEFER / FP / SECURITY / GLOBAL / ADJUDICATION handlers

S7  EVOLUTION_QUEUE_BUILD
      mechanical from disposition receipts only

S8  EVOLUTION_HANDOFF
      after ALL MUST_FIX members durably CLOSED_FIXED
      → exact queued tails to /opsx-evolution (Owner paste / dispatch)

S9  OPTIONAL_DEBT_BOARD_SANITY
      mission-scoped /opsx-bug-debt-board READ_ONLY
      → MISSION_OWNERSHIP_INCONSISTENCY may block clear
      → unrelated project debt MUST NOT block

S10 BUG_BATCH_CLEAR_EVAL
      mission-scoped predicate (below)

S11 RETURN_TO_CALLER_DISCOVERY_GATE
      do NOT auto-run /opsx-project-bug-hunt from Bug Team NEXT_ACTION
```

Orch MUST NOT model Bug Team WAVE_1–WAVE_4 as S4 sub-stages.

## Autonomous control loop

```text
AUTONOMOUS_POLL_HARVEST_LOOP=YES
OWNER_WAKE_REQUIRED=NO
SILENT_BLOCKING_WAIT=FORBIDDEN
```

After each background dispatch:

1. Emit `RUNNING_WORKER` in Owner aggregate.
2. Poll/harvest using ETA sources from CORE (not UI Stopped).
3. On confirmed ETA → release lock → evaluate SSCA → advance DAG.
4. Continue until mission terminal or genuine Owner semantic gate.
5. When `AUTO_PERSIST=REQUIRED`, execute `DELEGATED_PERSIST` immediately — do not
   emit Owner-wake between READ_ONLY and persist.

## Worktree ownership reconcile (S3.5)

Mandatory before the first S4 dispatch for each MUST_FIX work item. Prevents
pre-existing dirty/partial repairs from being silently attributed to the wrong
work item.

```text
Inputs:
  family-plan work items + member LOC_LINKs
  disposition MINIMUM_CORRECT_REPAIR boundaries (when present)
  git dirty/untracked product paths (exclude .cursor/**, .tmp/**, openspec/**)

WORKTREE_DELTA_MAP:
  path → { work_item_id, member_id, provenance }

Rules:
  1. Each dirty product path maps to exactly one mission work item OR EXCLUDED_UNRELATED
  2. Ambiguous multi-item ownership → BLOCKED_WORKTREE_OWNERSHIP (semantic Owner gate)
  3. REUSE_PARTIAL_REPAIR allowed iff path ⊆ declared scope of target WORK_ITEM_ID
  4. CROSS_ITEM_ABSORPTION forbidden (WI-2 delta cannot be credited to WI-1)
  5. Bug Team dispatch envelope MUST include:
       WORK_ITEM_SCOPE_PATHS[]
       PREEXISTING_PARTIAL_REPAIR_PATHS[] (subset of scope only)
       FORBIDDEN_PATHS[] (other work items' deltas)
  6. Worker must test completed repair for *this* work item only
  7. Unrelated dirty paths remain visible but out of repair scope

Persist: receipts/worktree-ownership-<WORK_ITEM_ID>.json
Record paths on mission.worktree_ownership_receipts[]
```

## Routing matrix (S3)

| Disposition | Outer route |
|-------------|-------------|
| `MUST_FIX_CURRENT_CHANGE` | S4 Bug Team → S5 closure |
| `MUST_FIX` + `STRUCTURAL_DEBT_TAIL` | S4 repair now → S5 close → queue tail → S8 after all MUST_FIX closed |
| `TECH_DEBT_ONLY` | S8 Evolution directly; no Bug Team |
| `DEFER_PRODUCT_BUG` | S6 materialize backlog; not Evolution; not `CLOSED_FIXED` |
| `FALSE_POSITIVE` | S6 durable FP terminal; no Evolution |
| `GLOBAL_RELEASE_BLOCKER` | S6 separate urgent route; `BUG_BATCH_CLEAR=NO`; `BLOCKED_GLOBAL_BLOCKER` |
| `SECURITY_TRIAGE_REQUIRED` | S6 `/opsx-security-triage` handoff; no ordinary Bug Team by label alone |
| `NEEDS_ADJUDICATION` / `AUTHORITY_CONFLICT` | fail closed; Owner decision |
| `MIXED_DISPOSITION` | split by member ids; MUST_FIX subset → S4; others → respective S6 routes |

`DEBT_WAIVER_FORBIDDEN`: MUST_FIX repair cannot be waived because a debt tail exists.

## Bug Team opaque boundary (S4)

```text
BUG_TEAM_OPAQUE=YES
OUTER_TASKS_PER_BUG_TEAM_EPISODE=1
OUTER_WAVE_DUPLICATION=FORBIDDEN
```

Orch dispatches exactly:

```text
/opsx-bug-team plan=<PLAN_ID> work_item=<WORK_ITEM_ID> disposition=<DISPOSITION_ID> [member_ids=<MUST_FIX subset>]
```

After S3.5, dispatch MUST carry worktree scope from
`receipts/worktree-ownership-<WORK_ITEM_ID>.json` (`WORK_ITEM_SCOPE_PATHS`,
`PREEXISTING_PARTIAL_REPAIR_PATHS`, `FORBIDDEN_PATHS`).

Forbidden:

- outer Tasks for WAVE_1 / WAVE_2 / WAVE_3 / WAVE_4
- dispatch Bug Team internal workers directly
- reproduce RED/GREEN/fanout/fan-in/learning in Orch
- infer internal Bug Team findings from prose

Harvest **top-level** Bug Team result envelope only.

## Closure owner matrix (S5)

Consume durable state; do not assume Bug Team always/never auto-closes.

| Outcome | Orch action |
|---------|-------------|
| `BUG_TEAM_ALREADY_DURABLY_CLOSED` | Durable `closed_fixed` exists → **no** duplicate materialize → member repair terminal |
| `BUG_TEAM_READY_FOR_CLOSURE` | Required GREEN/fanin/learning at top-level; no durable `closed_fixed` → S5b `DELEGATED_PERSIST` `/opsx-materialize-bug-queue mode=update … close=fixed` when `AUTO_PERSIST=REQUIRED` → require `closed_fixed` |
| `BUG_TEAM_BLOCKED` / `FAILED` / `OWNER_GATE` | Propagate blocked; no fabricated close; no Evolution waiver of MUST_FIX |

Top-level tokens (from Bug Team boundary):

```text
STATUS: READY_FOR_CLOSURE
WORK_ITEM_GREEN: YES
FANIN_READY: YES
LEARNING_DONE: YES
```

## Evolution queue (S7–S8)

```text
EVOLUTION_QUEUE source: ONLY persisted /opsx-bug-disposition receipts
```

For every member where disposition receipt has:

```text
STRUCTURAL_DEBT_TAIL=YES
EVOLUTION_HANDOFF_REQUIRED=YES
```

enqueue **exact** payload with provenance:

```text
PLAN_ID
WORK_ITEM_ID
FAMILY_ID (if any)
MEMBER_ID
DISPOSITION_ID
DEBT_TAIL.problem
DEBT_TAIL.why_beyond_minimum_repair
DEBT_TAIL.evidence
DEBT_WAIVER_FORBIDDEN=YES
```

Rules:

- Do **not** semantically merge tails in Orch.
- Do **not** collapse 3 tails into 1 "broader audit".
- Do **not** create tail content from Bug Team learning (learning = evidence only).
- `/opsx-evolution` owns dedupe/merge/reconcile.

Persist queue: `evolution-queue.ndjson` (schema:
`.cursor/pipeline/orch-core/schemas/evolution-queue-entry.schema.json`).

Handoff timing:

```text
EVOLUTION_AFTER_ALL_MUST_FIX_CLOSES=YES
```

## Mixed-family semantics

- Preserve `FAMILY_ID` and full membership/provenance.
- Repair only MUST_FIX subset via `member_ids=`.
- `REPAIR_SUBSET_GREEN` ≠ `FAMILY_DISPOSITION_COMPLETE`.
- Never mark deferred/FP members `CLOSED_FIXED`.
- Never represent FP/deferred as repaired learning evidence.
- Do not flatten family into unrelated atomics for routing convenience.

## BUG_BATCH_CLEAR predicate (S10)

Mission-scoped only. Unrelated project bug debt MUST NOT affect this mission.

```text
BUG_BATCH_CLEAR=YES iff ALL of:
  every current mission work item has durable disposition receipt
  every MUST_FIX member is durably CLOSED_FIXED (or repair terminal blocked truthfully)
  every FALSE_POSITIVE has durable terminal state
  every DEFER_PRODUCT_BUG has durable backlog ownership
  every required TECH_DEBT_ONLY / queued DEBT_TAIL has Evolution handoff ownership
  no unresolved adjudication / security / global release blocker for this mission
  mixed-family ownership complete for mission members
  optional S9: no MISSION_OWNERSHIP_INCONSISTENCY when debt-board scoped to mission cases
```

When true:

```text
BUG_BATCH_CLEAR=YES
RETURN_TO_CALLER_DISCOVERY_GATE=YES
```

Do **not** recursively start `/opsx-project-bug-hunt` in v1.

## BUG_DEBT_BOARD_POLICY (S9)

```text
BUG_DEBT_BOARD_POLICY:
  optional mission-scoped sanity only
  never global debt gate
```

Allowed block:

```text
MISSION_OWNERSHIP_INCONSISTENCY
  e.g. repaired mission member still open in mission-scoped case map
```

Forbidden:

```text
unrelated project DEBT_FOUND → PARTIAL_COMPLETE (removed)
global open bug count blocking mission clear
```

## Async / liveness hard rules

Inherited from core + transport; scenario enforces:

```text
TASK_BACKGROUND primary for Bug Team hops
HOST_INLINE only explicit logged fallback
MAX_PRODUCT_WRITERS=1
UI_STOPPED_IS_TERMINAL=NO
TIMEOUT != EXECUTION_TERMINAL
STOP_UNCONFIRMED → no lock reuse, no retry, mission blocked
never two possibly-live product writers
```

## Owner-facing output

Emit compact aggregate (schema:
`.cursor/pipeline/orch-core/schemas/owner-aggregate-output.schema.json`):

```text
FIX_NOW:
DEFERRED_PRODUCT_BUGS:
FALSE_POSITIVES:
TECH_DEBT_ONLY:
DEBT_TAILS:
SECURITY:
GLOBAL_BLOCKERS:
UNRESOLVED:
CHANGE_CLEAR:
RELEASE_CLEAR:
BUG_BATCH_CLEAR:
RUNNING_WORKER:
NEXT_REAL_ACTION:
```

### Owner aggregate clear-flag passthrough

`CHANGE_CLEAR` and `RELEASE_CLEAR` MUST mirror persisted `/opsx-bug-disposition`
receipt fields — **not** be independently recomputed by Orch.

```text
AGGREGATE_SOURCE: disposition receipt fields only
AGGREGATE_RECOMPUTE: FORBIDDEN

RELEASE_CLEAR=false with only MUST_FIX_CURRENT_CHANGE members (no GLOBAL_BLOCKER,
no SECURITY) is disposition-correct per /opsx-bug-disposition authority.
```

Owner intervention only for genuine semantic/risk ambiguity — not routine
`DELEGATED_PERSIST` on family-plan, disposition, or materialize closure.

## Failure / blocked states

| Status | Meaning |
|--------|---------|
| `BLOCKED_OWNER_DECISION` | adjudication / authority conflict |
| `BLOCKED_WORKER_STATE` | STOP_UNCONFIRMED after timeout |
| `BLOCKED_RESOURCE` | lock held with unconfirmed worker |
| `BLOCKED_GLOBAL_BLOCKER` | global release blocker in batch |
| `BLOCKED_WORKTREE_OWNERSHIP` | ambiguous dirty-path → work-item mapping (S3.5) |
| `PARTIAL_COMPLETE` | some items terminal; batch not clear; or process-only handoff |
| `PROCESS_HANDOFF` | intake routed process findings to meta-fix; no product repair admitted |
| `ACTIVE` | orchestration running (never `ACTIVE — awaiting GO` when AUTO_PERSIST=REQUIRED) |

## Restart / recovery

1. Load `opsx/runs/orch-missions/<MISSION_ID>/mission.json`.
2. Reconcile non-terminal `workers/*.json` before new dispatch.
3. If any worker `STOP_UNCONFIRMED` → remain blocked; list in report.
4. Resume DAG from first incomplete stage without duplicate outer Tasks.

## Output contract

```text
STATUS: ACTIVE | BUG_BATCH_CLEAR | PARTIAL_COMPLETE | PROCESS_HANDOFF | BLOCKED_* | FAIL_CLOSED
MODE: ORCHESTRATING
COMMAND: /opsx-orch-bug-fix
MISSION_ID:
CHANGE_ID:
CURRENT_STAGE:
MISSION_STATUS:

OWNER_AGGREGATE:
  (fields per schema)

RUNNING_WORKER:
  DISPATCH_ID / TASK_ID / WORK_ITEM_ID / COMMAND / TRANSPORT

RECEIPTS:
  - stage receipts under opsx/runs/orch-missions/<MISSION_ID>/receipts/

WHAT_HAPPENED:
WHY:
OWNER_DECISION_REQUIRED: (semantic gates only)
NEXT_REAL_ACTION:
DO_NOT_DO_YET:
```

## Hard rules

* Pre-repair disposition remains Orch responsibility — Bug Team requires persisted disposition.
* Bug Team is one opaque outer Task per episode.
* Do not follow Bug Team `/opsx-project-bug-hunt` NEXT_ACTION automatically in v1.
* Evolution queue mechanical from disposition only.
* Mission-scoped BUG_BATCH_CLEAR only.
* No modify committed neighbor commands from this file.
* No product edits from Orch host — workers only.
* `AUTO_PERSIST=REQUIRED` → immediate `DELEGATED_PERSIST`; never Owner-wake for routine persist.
* `CHANGE_CLEAR` / `RELEASE_CLEAR` passthrough from disposition receipts only.
* S3.5 worktree ownership reconcile before first S4 dispatch per work item.
* Bare invocation: R0–R3 source precedence; terminal prior mission must not
  shadow fresh immediate findings.
* OPSX command/process-contract findings → `/opsx-meta-fix-command-defects`, not
  this command's product bug PP.
* S0 MUST consume intake `PROCESS_FINDINGS_ROUTED` — never run family-plan /
  disposition / bug-team on `.cursor/commands/**` or `opsx/**` process rows.

## REQUIRED_EXTERNAL_FIXES (not applied here)

Hand off to `/opsx-meta-fix-command-defects` when needed:

1. Optional companion doc on `/opsx-bug-team` clarifying outer-orchestrator opaque boundary and `WORK_ITEM_SCOPE_PATHS` consumption (if Owner wants explicit cross-link).
2. Optional `/opsx-bug-debt-board` mission-scoped filter token documentation.
3. Phase 1b validation: Task stop/cancel + `WORKER_NOT_RUNNING` probe on Owner Cursor host.

## Phased implementation note

Phase 1 = this command + orch-core contracts + fixtures/tests.  
Phase 2 = async transport smoke/measurement on live Cursor host.  
Future `/opsx-orch-*` scenarios reuse core without copying mechanics.
