---
component_id: opsx.commands.meta_review_langgraph
component_version: 0.1.0
ownership: TARGET_ONLY
runtime_class: ACTIVE_WITH_LIMITS
local_modification_note: "READ_ONLY architecture review of OPSX LangGraph runtime (workflows + agents)"
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
contract_id: OPSX-WB-META-REVIEW-LANGGRAPH-01
newline_normalization: none
description: "Owner scopes LangGraph review in plain language; command audits workflows/agents/wiring and emits prioritized findings + follow-up meta commands; default READ_ONLY."
argument-hint: "<что смотреть — весь runtime / verify / repair / агент X>; опционально SCOPE=, FOCUS="
---

# STROY-SYSTEM TARGET OVERRIDE - HIGHEST PRIORITY

For stroy-system, product execution resolves through `opsx/project/capabilities.yaml`.
Donor literals in inherited text are not execution authority.

# TARGET OVERRIDE — HIGHEST PRIORITY

**Contract:** `OPSX-WB-META-REVIEW-LANGGRAPH-01`
**Runtime class:** `ACTIVE_WITH_LIMITS`

```text
IMPLEMENTATION_STATUS: IMPLEMENTED_UNVERIFIED
DEFAULT_MODE: READ_ONLY
WRITES: NONE
NOT_COMMITTED_THIS_RUN: true
```

### Authority root

```text
LANGGRAPH_ROOT: opsx/opsx_runtime/
  workflows/     # graphs, subgraphs, routing
  agents/        # hop packages (verify, repair, implementation); Cursor SDK in adapters/
  configs/       # model roles (read-only context)
```

**Out of scope** (redirect, do not score as LangGraph gaps):

| Topic | Route |
|-------|--------|
| OpenSpec change verify / archive GO | `/opsx-verify`, product verify |
| Cursor slash commands only | `/opsx-meta-chat-command-audit`, fix-command-defects |
| bug-team Task workers without graph | `.cursor/pipeline/bug-team/` + consult |
| Temporal worker wiring only | `tools/temporal/` — note boundary, no deep audit unless Owner scopes it |
| `src/stroy` product code | product lane |

### Related meta chain

- `/opsx-meta-add-agent` — new role
- `/opsx-meta-wire-workflow-agent` — wiring plan
- `/opsx-meta-refactor-agent` — refactor plan
- This command — **audit first**, then route to those owners

### Projectization gate

Incomplete `opsx/projectization.yaml` → `FAIL_CLOSED` → `/opsx-projectize`

### Hard bans (this command)

- No file edits, no pytest live runs, no `run_opsx_main.py` verify unless Owner
  explicitly passes `RUN_LIVE:` (out of default scope).
- No commit, push, archive, deploy.
- Findings must cite **read paths**; no invented defects.

### Hard stop after READ_ONLY (mandatory)

- **Запрещены** в этом invocation: `StrReplace` / `Write` по `opsx/opsx_runtime/**`,
  `src/**`, product entrypoints.
- Owner traceback / симптом поломки → только `FINDINGS` + `suggested_next:
  /opsx-meta-repair-langgraph` (или wire, если gap плановый).
- Если агент уже начал product fix без Owner GO on repair → откат + в отчёте
  `REVIEW_ONLY_VIOLATION: code edits attempted`.

### Lexicon: SDK (Subagent/Task — mandatory)

**`SDK` = Cursor SDK only** in review findings and routed briefs:
`agents/adapters/cursor_sdk/*`, `GetRun`, stream, `run.wait`,
**`agent.run` (Cursor SDK transport)**, `SDK_*` recovery when Cursor dispatch is live.

| Forbidden loose phrasing | Use instead |
|--------------------------|-------------|
| «SDK hop» for plan hop, `implementation_execute_hop_node`, `hop_lane_status`, stagnation | **implementation hop**, **execute hop**, **mutating hop**, **OPSX implementation lane** |
| «SDK RCA» for orchestration / lane status | **orchestration RCA** first; Cursor SDK RCA only in `cursor_sdk/*` |

Live Cursor dispatch: **mutating hop via Cursor SDK** — not a synonym for graph orchestration.
SoT: `.cursor/commands/opsx-meta-fix-command-defects.md` § Lexicon: SDK.

---

# /opsx-meta-review-langgraph

**Architecture / consistency review** LangGraph OPSX runtime: целый проект или
часть (verify graph, repair graph, один агент, dispatch cycle, …).

Owner задаёт scope **свободным текстом**. Команда читает bounded I/O, строит
отчёт, предлагает **следующие meta-команды** — без правок в этом вызове.

Опционально: `SCOPE:`, `FOCUS:`, `PEER:`, `DEPTH: quick | standard | deep`.

## Owner phrase → scope (do not misread)

| Owner says | Review first (evidence in output) |
|------------|-----------------------------------|
| «главный граф», «выше verify», «в начало parent» | `workflows/main_graph.py` edge order; `run_implementation.py` / composition root — **not** verify subgraph internals first |
| «имплементация» (wiring context) | `IMPLEMENTATION_GRAPH` + parent mount in `build_main_graph` |
| «где подключён hop/агент», «где крутится scaffold» | **Mandatory wiring trace** (ниже) — **до** FINDINGS/SCORECARD |
| traceback до `ainvoke` / `run_dir` / `checkpoint` | invoke layer (`run_verify_impl`, `workflows/main.py`, `verify/run.py` parity) — route to repair, do not patch in review |

## Mandatory wiring trace (Owner «где подключён hop/агент»)

Если Owner спрашивает **где в workflow подключён** hop / агент (scaffold, red, …) —
ответ READ_ONLY **начинается** с блока `WIRING_TRACE:` (минимум 4 пункта, пути +
node id / функция), **не** с meta slash в `agent.py` и **не** с общих слов «в
implementation».

```text
WIRING_TRACE:
  1. Composition root: build_*_hop_agents / run_verify_impl (файл:строка)
  2. Parent graph node → subgraph (например apply_code_lane)
  3. Subgraph execute node (например implementation_execute_hop) + route loop
  4. _resolve_agent_for_stage → класс agents/.../agent.py; **нет** отдельного graph-node у hop-агента
```

Запрещено: вставлять `/opsx-meta-repair-langgraph` в docstring агента вместо trace.

## REVIEW_SCOPE (resolved from input)

| REVIEW_SCOPE | Typical paths |
|--------------|----------------|
| `FULL_RUNTIME` | `workflows/**`, `agents/**` (sampled), entry `workflows/main.py` |
| `VERIFY_GRAPH` | `workflows/verify/graph.py`, `gate_repair/`, `dispatch_cycle/`, `check_workflow.py` |
| `REPAIR_GRAPH` | `workflows/repair/graph.py`, `agents/repair/**` |
| `IMPLEMENTATION_GRAPH` | `workflows/implementation/**` |
| `CHECK_SUBGRAPH` | `check_workflow.py`, `check_workflow_receipt.py`, hunt/bot/security |
| `SINGLE_AGENT` | `agents/<lane>/<role>/` + grep callers in `workflows/**` |
| `CUSTOM` | Owner-named paths under `LANGGRAPH_ROOT` only |

If scope ambiguous → **≤3** `AskQuestion` (full verify vs one agent vs custom path).

## Review dimensions (all applicable to scope)

Score each: **PASS | WARN | FAIL | NOT_CHECKED** with evidence paths.

1. **Wiring colocation** — handlers and `route_*` live in same factory as
   `add_node` / `add_conditional_edges` (gold: `check_workflow.py`); parent
   `graph.py` thin. Flag scatter in `green_loop.py`, `pipeline_routing.py` without
   factory ownership.

2. **Agent package shape** — `ABCAgent`, `agent.py`, `schemas.py`,
   `prompt/get_prompt.py`, preflight/report split; peer align (`impl_verify`,
   `diagnostic`). Flag god-modules.

3. **Graph ↔ agent boundary** — workflow orchestrates; agents run bounded hops +
   schema; **mutating hop via Cursor SDK** / **agent.run (Cursor SDK transport)** only
   when the adapter dispatches live runs; no product writes in read-only agents.

4. **State & contracts** — `GraphState` keys documented by usage; Pydantic answers;
   receipt keys align with `VERIFY_PHASES` / repair README where relevant.

5. **Registration hooks** — dual `receipt=` + `register_check_workflow_receipt`
   documented when check workflows involved; no orphan `is_established`.

6. **Checkpoint node ids** — stable string ids; `_node` suffix convention per
   `workflows/verify/graph.py` module docstring.

7. **Tests** — presence of `workflows/**/tests/`, `agents/**/tests/` for scoped
   graphs; not a coverage % gate.

8. **Legacy & anti-patterns** — `.cursor/agents`, universal workers, legacy CLI
   paths in runtime (grep `agent create-chat` only to report).

9. **Cross-boundary** — Temporal obligations declared but graph hop missing
   (e.g. archive_verify); note as **GAP** not CRITICAL unless Owner scoped
   production readiness.

## Depth budget (I/O)

| DEPTH | Budget |
|-------|--------|
| `quick` | ≤8 files + ≤5 greps |
| `standard` (default) | ≤20 files + key greps |
| `deep` | ≤40 files; Owner must accept longer turn |

Never full recursive dump of `agents/**` without narrow FOCUS.

## Intake steps

1. Resolve `REVIEW_SCOPE` + `DEPTH`.
2. Projectization gate.
3. Build **file set** from scope table + Owner hints.
4. Run dimension checks; record `FINDINGS[]` with
   `severity: CRITICAL | WARNING | SUGGESTION`, `path`, `summary`, `recommendation`.
5. **«КАК ПОНЯЛ»** — scope + what was not read.
6. **Aggregate scorecard** + **prioritized follow-ups** mapping finding → meta command:

| Finding type | Typical NEXT_ACTION |
|--------------|---------------------|
| Scattered routing | `/opsx-meta-wire-workflow-agent` or refactor |
| Failing hop / red test | `/opsx-meta-repair-langgraph` |
| Agent shape drift | `/opsx-meta-refactor-agent` |
| Missing agent/hop | `/opsx-meta-add-agent` then wire |
| Policy/doc only | `/opsx-meta-fix-command-defects` if committed doc |
| Unclear ownership | `/opsx-meta-consult` |

7. **Do not** run follow-up commands in same turn.

## CRITICAL heuristic (architecture)

Use **CRITICAL** sparingly:

- Breaks colocation doctrine on **hot path** (new hop wired only in `graph.py`,
  routing-only in distant module **with no ticket in plan**).
- Read-only agent performs writes or archive authorize.
- Missing schema validation on **mutating hop** that changes repo (Cursor SDK = transport only).
- Duplicate conflicting `route_*` for same node id.

Prefer **WARNING** for DRY, test gaps, peer misalign. **SUGGESTION** for style.

### Implementation subgraph checklist (when `workflows/implementation/**` in scope)

- Duplicate **plan** nodes (design lane `*_route` + main `implementation_route`)
  without `plan reuse from design_lane` path?
- `*_begin` nodes that only bootstrap state — merge candidate?
- Conditional edge maps: router returns **semantic** keys, not opaque `node: node`?
- Nested subgraph state: flags like `apply_design_lane_complete` on `GraphState`?

## Diagnose visual impl (before Cursor provider RCA)

Когда Owner жалуется на «визуальную» имплементацию / залипший apply-hop — **не**
начинать с Cursor API / provider RCA. Порядок диагностики:

1. **Сначала (product lane / receipts):**
   - `apply_*_status`, `implementation_recovery_code`
   - `hop_lane_status.py` — семантика `NO_CHANGE` / `BLOCKED` / `APPLY_COMPLETE`

2. **Второй слой (plan + design on disk):**
   - node plan `next_stage`
   - design snapshot `STALE` vs fresh
   - machine validator **PASS** на диске (артефакты, не stream)

3. **Cursor SDK / stream / reconcile** — только если hop status **OK**, но dispatch
   всё ещё blocked (adapter/transient), не как первая гипотеза.

4. **«Cursor SDK adapter»** ≠ чинить Cursor API; по умолчанию правим **product
   logic layer** (mapping hop outcomes, lane advance, preservation prompts).

## Output contract

```text
STATUS: REVIEW_COMPLETE | BLOCKED_NEEDS_INPUT | FAIL_CLOSED
MODE: READ_ONLY
REVIEW_SCOPE:
DEPTH:
SCOPE_PATHS_READ:
SCORECARD:
  wiring_colocation: PASS|WARN|FAIL|NOT_CHECKED
  agent_package_shape: ...
  graph_agent_boundary: ...
  state_contracts: ...
  registration_hooks: ...
  checkpoint_ids: ...
  tests: ...
  legacy_anti_patterns: ...
  cross_boundary: ...
FINDINGS:
  - severity: CRITICAL|WARNING|SUGGESTION
    id: LG-001
    path: <file or graph area>
    summary:
    recommendation:
    suggested_next: /opsx-meta-*
FOLLOW_UP_QUEUE:
  - <ordered meta commands with one-line why>
NOT_READ:
  - <paths skipped due to budget>
ORIENTATION_HINT:
  LANE: META
  NEXT_META: /opsx-meta
  NEXT_ACTION: <first item from FOLLOW_UP_QUEUE or none>
```

## Optional live probe

Only if Owner message contains exact token `RUN_LIVE:`:

- Allowed: targeted `poetry run pytest` under `opsx/opsx_runtime/` for named
  test module — report result, still no code edits.
- Forbidden: full verify funnel, destructive tests, without explicit path.

Default: **no shell** beyond `git`/`rg` read helpers if needed.

## Failure / recovery

| Condition | STATUS |
|-----------|--------|
| Scope outside `LANGGRAPH_ROOT` | FAIL_CLOSED + redirect |
| Empty input | BLOCKED_NEEDS_INPUT |
| Owner wants product code review | explain out of scope |

## Anti-patterns

- Fixing code during review
- Claiming FULL_RUNTIME PASS on quick depth
- Routing to `/opsx-archive` or authorize GO
- Duplicate of `/opsx-verify` product archive audit

## Meta chain (four commands)

```text
/opsx-meta-review-langgraph       → audit (this)
/opsx-meta-refactor-agent         → plan refactor
/opsx-meta-repair-langgraph       → plan fix (broken)
/opsx-meta-wire-workflow-agent    → plan wiring
/opsx-meta-add-agent              → new role
```

Recommended flow: **review → repair or refactor or wire → GO on fix owner → debrief**.
