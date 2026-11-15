---
component_id: opsx.commands.meta_repair_langgraph
component_version: 0.1.0
ownership: TARGET_ONLY
runtime_class: ACTIVE_WITH_LIMITS
local_modification_note: "Universal LangGraph runtime repair intake: workflow + agents + tests; RCA-first; APPLY via meta-fix after GO"
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
contract_id: OPSX-WB-META-REPAIR-LANGGRAPH-01
newline_normalization: none
description: "Owner describes what is broken in OPSX LangGraph runtime; command diagnoses workflow/agent/test layers, emits REPAIR_PLAN, routes to fix owner; intake READ_ONLY."
argument-hint: "<что сломано — свободный текст>; опционально TEST=, AGENT=, WORKFLOW=, LOG=, RUN_TESTS:"
---

# STROY-SYSTEM TARGET OVERRIDE - HIGHEST PRIORITY

# TARGET OVERRIDE — HIGHEST PRIORITY

**Contract:** `OPSX-WB-META-REPAIR-LANGGRAPH-01`
**Runtime class:** `ACTIVE_WITH_LIMITS`

```text
SCOPE: opsx/opsx_runtime/ (workflows + agents + their tests)
DEFAULT_MODE: READ_ONLY
UNIVERSAL: workflow | agent | tests | cross-layer
NOT_COMMITTED_THIS_RUN: true
```

---

# /opsx-meta-repair-langgraph

**Универсальный ремонт** OPSX LangGraph runtime: сломан **workflow**,
**агент**, **тесты** или связка слоёв — одна точка входа.

**Рефактор** (`/opsx-meta-refactor-agent`) — когда «работает, но криво».  
**Ремонт** (эта команда) — когда **есть симптом поломки** (fail, loop, wrong route,
schema error, flaky test, verify hop PAUSE).

Owner описывает **свободным текстом**; можно приложить traceback, путь к тесту,
лейбл verify. ≤3 вопроса только при блокере.

## User input

```text
$ARGUMENTS
```

```text
/opsx-meta-repair-langgraph
verify падает на archive_verify — KeyError в graph state, тест test_archive_verify_agent красный
```

```text
/opsx-meta-repair-langgraph
После wire project_bug conditional уходит не в bugbot а в END
RUN_TESTS: workflows/verify/tests/test_check_workflow_routing.py
```

Опционально: `TEST:`, `AGENT:`, `WORKFLOW:`, `SYMPTOM:`, `LOG:`, `CHANGE_ID:`,
`RUN_TESTS:` (exact pytest path under `opsx/opsx_runtime/`), `GO`.

## REPAIR_SURFACE (internal)

| Surface | Paths | Typical symptoms |
|---------|-------|------------------|
| `WORKFLOW` | `workflows/**/graph.py`, `subgraph.py`, `check_workflow.py`, routing helpers | wrong edge, missing node, END loop, checkpoint id mismatch |
| `AGENT` | `agents/**/agent.py`, schemas, preflight, prompt, adapters | Cursor adapter/transport error (`GetRun`, stream), bad JSON schema, preflight False negative |
| `TEST` | `workflows/**/tests/`, `agents/**/tests/` | pytest fail, stale mock, wrong graph compile assertion |
| `CROSS` | ≥2 surfaces with shared root cause | fix order required in plan |
| `COMMAND_META` | not this command — `.cursor/commands/opsx-meta-*.md` | `/opsx-meta-fix-command-defects` |

## Out of scope (redirect)

| Request | Route |
|---------|--------|
| Product bug `src/stroy` | `/opsx-fix-bug`, product pytest |
| Whole-repo pytest triage | `/opsx-test-triage`, `/opsx-static-check` |
| OpenSpec verify verdict | `/opsx-verify` |
| Structural cleanup, no failure | `/opsx-meta-refactor-agent` |
| New hop / new role | `/opsx-meta-add-agent` + wire |
| Audit only, nothing broken | `/opsx-meta-review-langgraph` |
| Slice number / label **next to current implementation procedure name** (CLI `procedure=`, `cli_outcome`, orchestration logs) | **`APPLY_FIX_RUNTIME`** — `routing_contract`, `cli_outcome`, `workflows/**/code_pipeline/graph.py` (`procedure=`); **not** `/opsx-meta-fix-command-defects`; **forbidden** `/opsx-meta-*` in `agents/**/agent.py` docstrings |

### Owner «рядом с apply_scaffold / hop» — disambiguation (mandatory)

Subagent, Task, and implement hops **must** use this table before any runtime edit:

| Owner phrase | Correct surface | Forbidden misread |
|--------------|-----------------|-------------------|
| номер slice / slice рядом с **именем процедуры** / `procedure=` | `implementation_procedure` / `format_current_implementation_procedure` in workflow + CLI; graph logging in `code_pipeline/graph.py` | paste `/opsx-meta-repair-langgraph` (or any `/opsx-meta-*`) into `opsx/opsx_runtime/agents/**/agent.py` module docstring |
| fix hop / repair apply_scaffold / «эти дебилы» (runtime symptom) | `/opsx-meta-repair-langgraph` **in chat** → `REPAIR_PLAN` → Owner `GO` → `APPLY_FIX_RUNTIME` | satisfy «рядом с X» by meta slash citation in agent package |
| process command debt only | `/opsx-meta-fix-command-defects` on `.cursor/**` | same chat interpreted as runtime repair via agent docstring |
| **задокументируй команду** (explicit) | `.cursor/commands/**` via fix-command-defects on `GO` | implicit docstring injection without those Owner words |

**Subagent/Task ban:** never interpret «рядом с hop/agent» as «add meta command to module docstring» unless Owner literally asked to **задокументируй команду** (process files only).

### Lexicon: SDK (Subagent/Task — mandatory)

**`SDK` = Cursor SDK only** in repair intake and `REPAIR_PLAN` text:
`cursor_sdk/*`, transport, `GetRun`, stream, `run.wait`,
**`agent.run` (Cursor SDK transport)** / **mutating hop via Cursor SDK**;
`SDK_ACTIVE_RUN_CONFLICT` and other `SDK_*` recovery codes vs **live Cursor runs**
/ dispatch ledger.

| Forbidden loose label | Say instead |
|----------------------|-------------|
| «SDK hop» for `implementation_execute_hop_node`, plan hop, `hop_lane_status`, stagnation | **implementation hop**, **execute hop**, **mutating hop**, **OPSX implementation lane** |
| «SDK RCA» when symptom is orchestration gates / lane status | **orchestration RCA** first (**L-006**); Cursor SDK RCA only inside `cursor_sdk/*` |

Subagent/Task instructions MUST NOT write **SDK hop** for
`implementation_execute_hop_node` unless discussing **Cursor adapter** invocation only.

## Procedure (intake)

1. Projectization gate.
2. **Normalize** → `SYMPTOM`, `SUSPECT_SURFACES[]`, `EVIDENCE_REFS[]`.
3. **RCA** (mandatory if error cited — per `AGENTS.md`):
   - read first meaningful failure (traceback, assertion, log line);
   - chain to file:line in workflow / agent / test;
   - separate harness vs product vs runtime wiring.
4. **Bounded read** (≤12 files): failing test, callee agent/workflow, one peer
   that still passes.
5. **`REPAIR_PLAN`**:
   - `ROOT_CAUSE` (one sentence + path:line)
   - `REPAIR_SLICES[]` ordered (often: agent → workflow → test, or test-led TDD fix)
   - per slice: `surface`, `files`, `change intent`, `risk`
   - `VERIFY_AFTER`: pytest paths or manual hop label
   - `COLLOCATED_RULE`: if fix touches routing, must stay in factory block (wire doctrine)
5a. **Visual / Cursor SDK failures** (adapter vs workflow — mandatory order):

   **L-006 — diagnose workflow first:** до `cursor_sdk/*` и **Cursor SDK** stream RCA
   проверить
   слой orchestration: `hop_lane_status`, plan / receipt, machine gates в
   `workflows/**` (not adapter labels as source of truth).

   **L-007 — SDK symptoms stay in adapter:** `GetRun`, stream stall, reconcile,
   terminal envelope, `run.wait` — чинить **внутри** `cursor_sdk/*` или уточнить
   **adapter API contract**;    **запрещено** размазывать **Cursor SDK** transport logic в
   `graph.py` / implementation nodes.

   **Slice in logs (Owner ask):** domain slice / hop context — **orchestration
   INFO** на execute hop; **не** подменять adapter transport label как slice id.

   `REPAIR_PLAN` for `AGENT` + adapter surface MUST state **L-006** / **L-007**
   compliance in slice `intent` when symptom touches Cursor SDK or hop visuals.

6. **Disposition** (mandatory — never conflate surfaces):

   | DISPOSITION | Meaning | NEXT_ACTION |
   |-------------|---------|-------------|
   | `APPLY_FIX_RUNTIME` | Owner `GO` — правки `opsx/opsx_runtime/**`, tests in `verify_after` | Apply in chat or Owner local run; **запрещено** `/opsx-meta-fix-command-defects` |
   | `APPLY_FIX_PROCESS` | Только `.cursor/**`, rules, shared meta docs | `/opsx-meta-fix-command-defects GO` |
   | `NEEDS_WIRE_PLAN` | Wiring gap без поломки | `/opsx-meta-wire-workflow-agent` then re-repair |
   | `NEEDS_REFACTOR` | Debt after green | `/opsx-meta-refactor-agent` |
   | `BLOCKED` | Missing evidence | narrow question or more logs |

   **Owner override:** «исправь», «переделай», «GO» без слова command-defects →
   `APPLY_FIX_RUNTIME`, не только REPAIR_PLAN.

7. Do **not** apply fixes in default mode (READ_ONLY intake).

### Invoke / entry regressions (CROSS — mandatory when symptom matches)

If failure is **before** graph `ainvoke` (e.g. `ValueError: run_dir or checkpoint_path
is required`, missing `resume_scope`):

1. Compare composition root with **working peer** —
   `workflows/verify/run.py` (`WorkflowResumeScope`, `thread_id` bootstrap).
2. `REPAIR_PLAN` must list **kwargs delta** (`resume_scope`, `run_dir`,
   `checkpoint_path`) vs previous entry path.
3. `verify_after`: smoke `poetry run python -u run_opsx_main.py <change_id>` —
   not full verify funnel unless Owner asks.

### Carry-forward review findings

If the same chat already ran `/opsx-meta-review-langgraph` with open `LG-*` ids
(e.g. `LG-003 RECOVERY_PAUSED`), repair intake must **cite** each id as in-scope,
explicit `OUT_OF_SCOPE`, or deferred — no silent drop.

## Optional `RUN_TESTS:`

If Owner names exact path under `opsx/opsx_runtime/`:

```text
cd opsx/opsx_runtime && poetry run pytest <path> -q
```

Report exit code + first failure only. No full verify funnel. No `RUN_TESTS` without
Owner token.

## `GO` mode

Exact `GO` in message → still **no edits in this command** (intake). Emit:

```text
AUTHORIZE_LANGGRAPH_REPAIR: true
SOURCE_COMMAND: /opsx-meta-repair-langgraph
REPAIR_PLAN: (paste)
DISPOSITION: APPLY_FIX_RUNTIME | …
```

- **`APPLY_FIX_RUNTIME`:** implement `REPAIR_PLAN` slices under
  `opsx/opsx_runtime/**` in chat (Owner `GO`). **Do not** route to fix-command-defects.
- **`APPLY_FIX_PROCESS` only:** `/opsx-meta-fix-command-defects GO` with packet.

## Live verify_after

Do **not** run `run_opsx_main.py` from intake/repair READ_ONLY without Owner
`RUN_LIVE` / explicit live GO. List pytest paths in `verify_after` plus one
Owner-local tee command; agent-shell long runs abort unpredictably.

## Repair vs refactor (routing)

| Signal | Command |
|--------|---------|
| pytest red, traceback, wrong graph edge, runtime error | **repair** (this) |
| peer align, DRY, split module, no failing symptom | refactor |
| scattered routes only, compiles OK | wire |

## Output contract

```text
STATUS: REPAIR_PLANNED | BLOCKED_NEEDS_INPUT | FAIL_CLOSED
MODE: READ_ONLY
REPAIR_SURFACE: WORKFLOW | AGENT | TEST | CROSS
ROOT_CAUSE:
REPAIR_PLAN:
  slices:
    - id: R-001
      surface:
      files:
      intent:
      risk:
  verify_after:
  colocated_routing: yes | no | n/a
EVIDENCE_REFS:
ROUTING_PACKET:
DISPOSITION: APPLY_FIX_RUNTIME | APPLY_FIX_PROCESS | NEEDS_WIRE_PLAN | NEEDS_REFACTOR | BLOCKED
WHY:
NEXT_ACTION: (runtime apply in chat) | /opsx-meta-fix-command-defects | /opsx-meta-wire-workflow-agent | …
DO_NOT_DO_YET:
- apply without Owner GO
- /opsx-meta-fix-command-defects for opsx/opsx_runtime graph/agent fixes
- `/opsx-meta-*` slash citations in `opsx/opsx_runtime/agents/**/agent.py` (or hop package) to satisfy «рядом с X» / procedure slice
- product src/** without redirect
- /opsx-archive
ORIENTATION_HINT:
  LANE: META
  NEXT_META: /opsx-meta
  NEXT_ACTION: <same>
```

## Failure / recovery

| Condition | STATUS |
|-----------|--------|
| Symptom only in product tests/ | redirect product lane |
| No reproducible symptom | BLOCKED or review |
| Fix requires new agent | add + wire, not repair alone |

## Anti-patterns

- Using repair for meta command typos (fix-command-defects)
- **NOT THIS:** putting `/opsx-meta-repair-langgraph` or any `/opsx-meta-*` in `agents/**/agent.py` module docstrings for «рядом с apply_scaffold» / fix-hop intent
- Routing **procedure/slice label** symptoms to fix-command-defects instead of `APPLY_FIX_RUNTIME` (`cli_outcome`, `code_pipeline/graph.py`, `routing_contract`)
- Broad refactor disguised as repair without root cause
- Moving routing to `green_loop.py` while «fixing» hop
- Full `run_opsx_main.py verify` in intake without Owner ask
- **L-006:** Cursor SDK RCA before **orchestration** (`hop_lane_status` / plan / machine gates)
- Loose **SDK hop** label for `implementation_execute_hop_node` (say **execute hop**)
- **L-007:** `GetRun` / stream / envelope fixes scattered into `graph.py`
- Logging adapter transport label when Owner asked for **slice** in orchestration logs

## Meta chain

```text
/opsx-meta-review-langgraph    → find issues
/opsx-meta-repair-langgraph     → fix broken (this)
/opsx-meta-refactor-agent       → cleanup after green
/opsx-meta-wire-workflow-agent  → if plan says wiring gap
/opsx-meta-debrief-chat         → harden commands after session
```

**Architecture-first:** repair must not violate colocated wiring; if fix would
scatter routes → slice 1 = move into factory, slice 2 = behavior fix.
