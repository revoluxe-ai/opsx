---
component_id: opsx.commands.meta_refactor_agent
component_version: 0.1.0
ownership: TARGET_ONLY
runtime_class: ACTIVE_WITH_LIMITS
local_modification_note: "Owner-first refactor intake for OPSX LangGraph runtime agents + related wiring (Cursor SDK = adapter transport only); routes to fix/design/wire owners"
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
contract_id: OPSX-WB-META-REFACTOR-AGENT-01
newline_normalization: none
description: "Owner describes agent refactor in plain language; command classifies scope, finds peers, emits REFACTOR_PLAN, routes to one owner; intake READ_ONLY."
argument-hint: "<что не так / к чему привести — свободным текстом>; опционально AGENT=, PEER=, SYMPTOM="
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


# /opsx-meta-refactor-agent

Рефакторинг **существующих** OPSX LangGraph runtime-агентов (и связанного wiring):
**не** путать с Cursor SDK — transport только в `cursor_sdk/*`. Выровнять
по peer, убрать дубли, собрать маршрутизацию в одном месте, починить контракт
schema/prompt/preflight/report — **без** смешивания с «создать нового».

Owner пишет **свободным текстом** (что болит, к какому образцу тянуть).
≤3 вопроса только при блокере.

## User input

```text
$ARGUMENTS
```

Примеры:

```text
/opsx-meta-refactor-agent
archive_verify раздут — приведи к каркасу impl_verify, вынеси лишнее из agent.py
```

```text
/opsx-meta-refactor-agent
diagnostic и group_error дублируют нормализацию findings — один shared helper
```

Опционально: `AGENT:`, `PEER:`, `SYMPTOM:`, `NON_GOALS:`, `GOAL:`.

## REFACTOR_KIND (internal)

| REFACTOR_KIND | Когда | Типичные файлы |
|---------------|-------|----------------|
| `PEER_ALIGN` | отстать от эталона (`impl_verify`, `diagnostic`) | `agent.py`, `preflight.py`, `schemas.py` |
| `PACKAGE_SPLIT` | god-module, смешаны preflight/report/prompt | новые модули **внутри** той же роли |
| `PACKAGE_MERGE` | микро-файлы без смысла | обратное split с осторожностью |
| `PROMPT_DEDUP` | копипаста в `get_prompt.py` / `common_prompt_data` | prompt + shared strings |
| `SCHEMA_CONTRACT` | Pydantic / receipt drift | `schemas.py`, workflow expectations |
| `ADAPTER_WIRING` | `ABCAgent.run`, adapter, stream label | `agent.py`, `adapters/` |
| `WIRING_COLOCATE` | маршрут не рядом с hop | → план + `/opsx-meta-wire-workflow-agent` или fix |
| `TEST_HARNESS` | тесты не отражают контракт | `tests/` |
| `DEAD_LEGACY` | `.cursor/agents`, duplicate hop | удаление с `LEGACY_WARNINGS` |

## Intake steps

1. **Normalize** → `AGENT_REF`, `PEER_REF`, `SYMPTOM_SUMMARY`, `GOAL_SUMMARY`.
2. **Locate agent** — glob `agents/**/<name>/`; if missing →
   `STATUS: FAIL_CLOSED` `REASON: AGENT_NOT_FOUND` → maybe wrong repo or use add-agent.
3. **Bounded read** (budget ≤6 files):
   - `agent.py`, `schemas.py`, one of `preflight` / `prompt/get_prompt.py`
   - `PEER` same set if named or inferred
   - one test file if exists
   - grep imports of agent from `workflows/**` (who calls it)
4. **Classify** `REFACTOR_KIND` (+ secondary if needed).
5. **«КАК ПОНЯЛ»** (русский): что сломано, целевое состояние, риски.
6. **`REFACTOR_PLAN`**:
   - `FILES_TOUCH` ordered (minimal diff philosophy)
   - `KEEP_BEHAVIOR` / `ALLOW_BEHAVIOR_CHANGE` (default KEEP unless Owner said otherwise)
   - `PEER_DIFF_SUMMARY` (bullet: peer has X, subject lacks Y)
   - `WIRING_FOLLOWUP` yes/no → wire command
   - `TESTS_TO_UPDATE`
   - `ROLLBACK_NOTE`
7. Route **one** owner (below).

## Refactor vs fix-command-defects

| Situation | Owner |
|-----------|--------|
| Process **command** `.md` defect | `/opsx-meta-fix-command-defects` |
| **Agent package** structure / DRY / peer align | This command → **`REFACTOR_PLAN`** → fix with `AUTHORIZE_AGENT_REFACTOR` |
| Greenfield reshape (new role boundary) | `/opsx-meta-design-command` after plan |

Downstream fix uses packet header:

```text
AUTHORIZE_AGENT_REFACTOR: true
SOURCE_COMMAND: /opsx-meta-refactor-agent
REFACTOR_KIND:
AGENT_REF:
REFACTOR_PLAN: (paste)
```

Owner **GO** on fix owner required before edits to paths in `HEAD`.

## Implementation routing

| Situation | ROUTE_CLASS | NEXT_ACTION |
|-----------|-------------|-------------|
| Agent in HEAD, localized refactor | `REFACTOR_FIX` | `/opsx-meta-fix-command-defects` + packet above |
| Needs new module boundaries / ADR | `REFACTOR_DESIGN` | `/opsx-meta-design-command` + `REFACTOR_PLAN` |
| Primary issue is scattered graph routes | `REFACTOR_WIRE` | `/opsx-meta-wire-workflow-agent` with symptom |
| Agent should not exist / replace with command | `REDIRECT` | consult + add/fix |
| Symptom in chat only, no path | `BLOCKED_NEEDS_INPUT` | AskQuestion: agent name or path |

Do not run downstream in same turn.

## Colocated wiring (refactor angle)

If symptom is «маршрут далеко от узла» — `REFACTOR_PLAN` MUST cite
**COLLOCATED WIRING DOCTRINE** from `/opsx-meta-wire-workflow-agent` and name
`TARGET_FILE` to move logic into — not only patch call sites.

## Output contract

```text
STATUS: ROUTED | BLOCKED_NEEDS_INPUT | FAIL_CLOSED
MODE: READ_ONLY
REFACTOR_KIND:
AGENT_REF:
PEER_REF:
REFERENCE_MAP:
SYMPTOM_SUMMARY:
GOAL_SUMMARY:
REFACTOR_PLAN:
  FILES_TOUCH:
  KEEP_BEHAVIOR: yes | no
  PEER_DIFF_SUMMARY:
  WIRING_FOLLOWUP: yes | no
  TESTS_TO_UPDATE:
  ROLLBACK_NOTE:
ROUTE_CLASS:
ROUTING_PACKET:
WHY:
NEXT_ACTION:
DO_NOT_DO_YET:
- apply refactor in this command
- /opsx-meta-commit
- delete agent without Owner GO
ORIENTATION_HINT:
  LANE: META
  NEXT_META: /opsx-meta
  NEXT_ACTION: <same>
```

## Failure / recovery

| Condition | STATUS | NEXT |
|-----------|--------|------|
| Agent path not found | `FAIL_CLOSED` | `/opsx-meta-add-agent` if new role intended |
| Mixed agent + slash command | `BLOCKED_NEEDS_INPUT` | pick primary surface |
| Only tests fail, code OK | `REFACTOR_FIX` test-only slice in plan |

## Anti-patterns

- Full rewrite disguised as refactor without Owner `ALLOW_BEHAVIOR_CHANGE`
- Refactor agent + rewire graph in one mega-PR without wire plan
- Moving routing to `green_loop.py` while claiming colocation
- Using this command for brand-new `archive_verify` scaffold (use add-agent)
- Omitting tests in `REFACTOR_PLAN`
- **L-006:** OPSX / verify state / `apply_*` в `cursor_sdk/*` при peer-align
- **L-007:** Cursor SDK stream/GetRun/wait/envelope в `workflows/implementation` ради короче diff
- Subagent/Task: **SDK hop** для plan/execute/stagnation — запрещено; **implementation hop** / **execute hop**

## Meta chain

```text
/opsx-meta-review-langgraph    → аудит (опционально перед refactor)
/opsx-meta-add-agent           → новая роль
/opsx-meta-wire-workflow-agent → LangGraph узлы (colocated)
/opsx-meta-refactor-agent      → улучшить существующую роль / wiring debt
/opsx-meta-repair-langgraph     → сломано (workflow / agent / tests)
/opsx-meta-debrief-chat        → после сессии: уроки → правки meta-команд
```
