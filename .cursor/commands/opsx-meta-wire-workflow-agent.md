---
component_id: opsx.commands.meta_wire_workflow_agent
component_version: 0.1.0
ownership: TARGET_ONLY
runtime_class: ACTIVE_WITH_LIMITS
local_modification_note: "LangGraph wiring front door: colocated declarative block doctrine (Owner 2026-11)"
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
contract_id: OPSX-WB-META-WIRE-WORKFLOW-01
newline_normalization: none
description: "Owner describes how a LangGraph agent hops; command plans colocated wiring (handlers + routing + add_node) and routes to one implement owner; intake READ_ONLY."
argument-hint: "<свободный текст: какой агент, в какой workflow, откуда/куда>; опционально AGENT=, WORKFLOW=, SIMILAR_TO="
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


# /opsx-meta-wire-workflow-agent

Подключить **существующий** LangGraph-агент (или deterministic hop) к workflow
**декларативно и в одном месте**: в одном factory-блоке должны жить handler(ы),
функции маршрутизации (`route_*` / `route_check`), `add_node`, `add_edge`,
`add_conditional_edges` — **до** того, как parent graph только монтирует subgraph.

Owner описывает задачу **свободным текстом**. Шаблонные поля не обязательны.
При блокирующей неясности — **≤3** вопроса (`AskQuestion`).

## User input

```text
$ARGUMENTS
```

Примеры:

```text
/opsx-meta-wire-workflow-agent
archive_verify как impl_verify, после frozen plan, read-only PHASE_ARCHIVE, при NO_GO — стоп, не archive
```

```text
/opsx-meta-wire-workflow-agent
Новый diagnostic hop уже в agents/ — воткни в gate_repair после repair_order, маршрут при PAUSE — в owner attention
```

Опциональные подсказки: `AGENT:`, `WORKFLOW:`, `WORKFLOW_TARGET:`, `NODE_ID:`,
`PREDECESSOR:`, `SUCCESSOR:`, `SIMILAR_TO:`, `NON_GOALS:`.

## WORKFLOW_TARGET (internal)

| WORKFLOW_TARGET | Primary builder | Typical parent |
|-----------------|-----------------|----------------|
| `VERIFY_MAIN` | `build_verify_graph` | verify CLI / Temporal verify loop |
| `VERIFY_CHECK_SUBGRAPH` | `build_check_workflow_graph` | embedded in VERIFY_MAIN |
| `VERIFY_GATE_REPAIR` | `build_gate_repair_graph` | VERIFY_MAIN `static_repair` node |
| `VERIFY_DISPATCH_CYCLE` | `workflows/verify/dispatch_cycle/subgraph.py` | inside gate repair |
| `REPAIR_MAIN` | `build_repair_graph` | repair runner |
| `REPAIR_BUG_TEAM` | `build_bug_team_graph` | repair README composition |
| `IMPLEMENTATION` | `workflows/implementation/graph.py` | super-implement lane |
| `NEW_FACTORY` | new `build_*_graph` in one module | when no existing factory fits |

## Parent vs default entry (mandatory intake)

Classify Owner ask before `WIRING_PLAN`; emit `WIRING_CLASS:` in output.

| WIRING_CLASS | Touch | Do NOT |
|--------------|-------|--------|
| `PARENT_TOPOLOGY` | `build_main_graph`, nested `implementation`, `route_main_after_implementation` | Flip default verify entry to always-mutate without receipt skip |
| `RECEIPT_GATE` | `super-implement/RUN.json`, `implementation_begin`, idempotent skip | CLI flags `skip_*`, `--with-implementation`, `--live-execution` as default product contract |
| `ENTRY_ONLY` | `run_verify_impl`, `run_implementation`, `workflows/main.py` invoke | New duplicate `build_*_parent_graph.py` when parent already in HEAD |

When `build_main_graph` parent is **already in HEAD**: allowed delta ∈
`INVOKE_PATH` | `RECEIPT_GATE` | `TEST` only — not a second parent graph file.

### Receipt idempotent skip (split-APPLY)

- SoT: `openspec/changes/<id>/super-implement/RUN.json` with `status: APPLY_COMPLETE`.
- Skip = short-circuit at `implementation_begin` / route — not verify-only bypass.
- Live gate: `RUN.json` / ledger — not new CLI attestation flags in wiring plans.

### Split-apply hop outcomes (mandatory)

При планировании wiring split-APPLY / implementation lane:

- **`NO_CHANGE`** после machine **PASS** + durable receipt обязан **advance lane**,
  не маппить в `APPLY_BLOCKED`.
- Цепочка runtime (имена для плана и grep, без scatter):
  `implementation_execute_hop_node` → `agent.run` → `merge_split_apply_hop_result`
  → `hop_lane_status` → `stagnation_patch_after_hop`.
- **Preservation prompts** в agent/prompt должны **1:1** совпадать с runtime
  mapping hop outcomes (не отдельная семантика в prompt).
- **Design redo** — только при `APPLY_GREEN`; **mid-slice** →
  `DESIGN_REDO_MID_SLICE_FORBIDDEN` (route/stop, не silent redo).
- После смены design digest: receipts **invalidate**; prefer **skip/patch**,
  не wipe-first пересборка артефактов.

## COLLOCATED WIRING DOCTRINE (Owner — non-negotiable)

When producing `WIRING_PLAN`, enforce this order **in the target source file**:

```text
1. NODE_IDS + constants (checkpoint string ids)
2. async/sync node handler(s) / partial(agent=…)
3. route_* functions (all conditional targets defined here or above)
4. optional: receipt / establish / register hooks declared beside factory
5. StateGraph builder: add_node → add_edge → add_conditional_edges
6. return builder.compile(...)
```

Parent file (`graph.py`) may **only**:

- instantiate agent(s) passed into factory;
- `builder.add_node("<name>", compiled_subgraph)`;
- **one** conditional from parent router to that node (or START/END chain
  already documented in VERIFY docs).

**Forbidden plan patterns** (must appear in `ANTI_PATTERNS_CAUTION` if detected
in existing code being extended):

- New `route_after_*` in `green_loop.py` without moving logic into the factory block
  being edited.
- Separate `add_node` in `graph.py` without the handler living in the same PR
  section as its `route_*`.
- Third file for routing-only helpers when the hop fits in the peer factory file.
- Default CLI flags instead of disk receipt for implementation skip (`--with-implementation`,
  `--skip-implementation`, `--live-execution` as wiring “fix”).

### Dual-consumer registration (check workflows)

If plan touches project_bug / bugbot / security style checks, **explicitly** state:

- `receipt=` passed into `build_check_workflow_graph` (inner skip/establish)
- `register_check_workflow_receipt(...)` (outer `cycle_checks.is_established`)

These are **not duplicates** — both belong in the wiring plan with comments.

## Intake steps

1. **Normalize** → `AGENT_REF`, `WORKFLOW_TARGET`, `PREDECESSOR_GUESS`,
   `SUCCESSOR_GUESS`, `ROUTING_RULES_SUMMARY`.
2. **Projectization** gate.
3. **Bounded recon:**
   - Confirm agent module path or deterministic node name.
   - Read **one** peer factory (`SIMILAR_TO` or nearest hop in same graph).
   - Grep `build_verify_graph` / target builder for existing node id collisions.
   - Read `agents/verify/graph_validation.py` or repair routing only if
     verify main-line conditional is affected.
4. **«КАК ПОНЯЛ»** (русский): где в графе, откуда вход, куда выходы, read/write.
5. If agent missing → prereq route (above).
6. Emit **`WIRING_PLAN`** (markdown inside `ROUTING_PACKET`) with:
   - `TARGET_FILE` (single primary; secondary only for thin parent wire)
   - ordered blocks per COLLOCATED DOCTRINE
   - `PARENT_WIRE` section if subgraph mounted in `graph.py`
   - `CHECKPOINT_NODE_IDS` list
   - `TESTS_TO_TOUCH` (e.g. `workflows/verify/tests/test_*_graph.py`)
   - `ANTI_PATTERNS_CAUTION`
7. Route **exactly one** implementation owner (no execution here).

## Implementation routing (after intake)

| Situation | ROUTE_CLASS | NEXT_ACTION |
|-----------|-------------|-------------|
| New wiring, agent package uncommitted | `WIRE_NEW_UNCOMMITTED` | `/opsx-meta-design-command` with `WIRING_PLAN` + agent brief if needed |
| New wiring, agent already in HEAD | `WIRE_NEW_COMMITTED_AGENT` | `/opsx-meta-design-command` or Owner **GO** inline implement in follow-up chat with `WIRING_PLAN` attached |
| Change existing graph routing in HEAD | `WIRE_FIX` | `/opsx-meta-fix-command-defects` `AUTHORIZE_COMMAND_FIX` + `WIRING_PLAN` |
| Need agent + wire together | `AGENT_THEN_WIRE` | `/opsx-meta-add-agent` then re-run **this** command |
| Not LangGraph (Cursor archive only) | `REDIRECT_CURSOR_VERIFY` | explain; `/opsx-verify` hop — not this command |
| Unclear graph | `CONSULT` | `/opsx-meta-consult` |

`opsx-meta-design-runtime-wire` **does not exist** yet — until added, design-command
carries the `WIRING_PLAN` as the implementation brief.

## Output contract

```text
STATUS: ROUTED | BLOCKED_NEEDS_INPUT | FAIL_CLOSED | PREREQ_MISSING
MODE: READ_ONLY
WORKFLOW_TARGET:
AGENT_REF:
REFERENCE_MAP:
  - <path> — <why>
PREREQ_MISSING: AGENT_PACKAGE | none
WIRING_PLAN:
  TARGET_FILE:
  PARENT_WIRE:
  NODE_IDS:
  ROUTE_FUNCTIONS:
  COLLOCATED_BLOCK_OUTLINE: |
    (pseudocode section headers only — not full impl in intake)
  TESTS_TO_TOUCH:
ANTI_PATTERNS_CAUTION: []
ROUTE_CLASS:
ROUTING_PACKET:
  (full pasteable brief for downstream owner)
WHY:
NEXT_ACTION:
DO_NOT_DO_YET:
- edit graph files in this command
- /opsx-meta-commit
- /opsx-archive / authorize archive
ORIENTATION_HINT:
  LANE: META
  NEXT_META: /opsx-meta
  NEXT_ACTION: <same>
```

## Failure / recovery

| Condition | STATUS | NEXT |
|-----------|--------|------|
| Empty input | `BLOCKED_NEEDS_INPUT` | пример свободного текста |
| Agent package not found | `PREREQ_MISSING` | `/opsx-meta-add-agent` |
| Owner wants only Cursor verify | `ROUTED` | `REDIRECT_CURSOR_VERIFY` explanation |
| Colliding node id without resolution | `BLOCKED_NEEDS_INPUT` | rename vs replace hop |

## Anti-patterns

- **NOT THIS:** adding `/opsx-meta-wire-workflow-agent` or any `/opsx-meta-*` to
  `agents/**/agent.py` module docstring because Owner said «рядом с hop» — wiring
  intake stays **chat slash**; procedure/slice labels belong in workflow+CLI apply
  (`implementation_procedure`, `cli_outcome`, `code_pipeline/graph.py`), not agent
  docstring meta links (unless Owner explicitly **задокументируй команду** →
  fix-command-defects on `.cursor/commands/**` only)
- Implementing wiring in the intake turn
- Scattered routing across `green_loop.py` + `graph.py` without colocation plan
- Wiring archive PHASE_ARCHIVE as graph node without `ArchiveVerifyAgent` package
- Universal subgraph that runs classify+fix+test
- Omitting tests section in `WIRING_PLAN`
- Naming `implementation_execute_hop_node` / `hop_lane_status` / stagnation **SDK hop**
  in Subagent/Task briefs — **execute hop** / **OPSX implementation lane**; **SDK** =
  `cursor_sdk/*` transport only (`GetRun`, stream, `SDK_ACTIVE_RUN_CONFLICT`)

## Meta chain

```text
/opsx-meta-review-langgraph    → аудит перед правками
/opsx-meta-add-agent           → новая роль
/opsx-meta-wire-workflow-agent → wiring LangGraph
/opsx-meta-refactor-agent      → выровнять / DRY существующего
/opsx-meta-repair-langgraph     → сломан wiring / hop / tests
/opsx-meta-debrief-chat        → после сессии: уроки → правки meta-команд
```

Re-run wire after scaffold. Messy agent package → refactor before re-wire.
