---
component_id: opsx.commands.meta_debrief_chat
component_version: 0.1.0
ownership: TARGET_ONLY
runtime_class: ACTIVE_WITH_LIMITS
local_modification_note: "Post-session chat debrief → architecture lessons → meta command patch plan (Owner 2026-11)"
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
contract_id: OPSX-WB-META-DEBRIEF-CHAT-01
newline_normalization: none
description: "After work in this chat: mine transcript for architecture failures and Owner corrections; emit COMMAND_PATCH_PLAN for meta/LangGraph commands; APPLY only via meta-fix after Owner GO."
argument-hint: "[свободный итог / что болело] [CHAT=<uuid>] [FOCUS=langgraph|meta|all] [GO]"
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


# /opsx-meta-debrief-chat

Запускать **после** завершённой сессии (агент создан, wired, отрефакторен,
reviewed — или Owner явно говорит «итог / debrief»).

Команда **перечитывает чат**, находит где ломалась **архитектура** (размазанный
wiring, путаница LangGraph vs Cursor verify, пропуск colocated doctrine, лишние
вопросы Owner, неверный meta-chain), и готовит **план правок команд**, чтобы
**в следующий раз с первого хода** делали правильно.

Owner может добавить короткий комментарий; **не обязан** — достаточно самого
transcript.

## User input

```text
$ARGUMENTS
```

```text
/opsx-meta-debrief-chat
```

```text
/opsx-meta-debrief-chat
Главное: три раза объяснял colocated routing — вшей в wire-команду жёстче
FOCUS: langgraph
```

Опционально:

- `CHAT=<uuid>` — родительский transcript, если debrief не в том же чате
- `FOCUS: langgraph | meta | all` (default `langgraph` when `opsx/opsx_runtime` touched)
- exact `GO` — **не** пишет команды здесь; см. Modes

## Modes

| Mode | Trigger | Writes |
|------|---------|--------|
| `READ_ONLY` | default | never |
| `GO` | exact token `GO` in message | never; emit `AUTHORIZE_META_DEBRIEF_FIX` packet for fix owner |

`GO` does **not** authorize direct edits in this command.

## Evidence sources (priority)

1. **Current chat** (full thread in context).
2. If `CHAT=` or transcript path named — read
   `agent-transcripts/<uuid>/*.jsonl` for **this workspace** only
   (same rules as `opsx-meta-chat-command-audit.md` § transcripts).
3. **Git diff** in session (optional, bounded): only paths under
   `opsx/opsx_runtime/`, `.cursor/commands/opsx-meta-*.md` if Owner cites them.
4. **Do not** invent failures without quoted or paraphrased Owner correction /
   visible assistant mistake in chat.

## Not this command

| Need | Use |
|------|-----|
| Batch audit many old chats | `/opsx-meta-chat-command-audit` |
| Single Bugbot process finding | `/opsx-meta-promote-process-lesson` |
| LangGraph code review only | `/opsx-meta-review-langgraph` |
| Product bug | `/opsx-fix-bug` |

## Architecture-first lens (mandatory extraction)

For each failure, tag `ARCH_LAYER`:

| ARCH_LAYER | Meaning |
|------------|---------|
| `META_CHAIN` | wrong order add/wire/refactor/review; wrong command chosen |
| `LANGGRAPH_WIRING` | scatter vs colocated; parent graph fat; green_loop leakage |
| `AGENT_PACKAGE` | ABCAgent shape, preflight/prompt split, peer drift |
| `BOUNDARY` | LangGraph vs `/opsx-verify` / Cursor slash / Temporal |
| `INTAKE_UX` | over-questioning; ignored free-text; template fatigue |
| `COMMAND_CONTRACT` | missing guard, stale example, contradicting peer command |

## Protected command surfaces (patch targets)

Default patch map when `FOCUS` includes langgraph or meta:

```text
.cursor/commands/opsx-meta-add-agent.md
.cursor/commands/opsx-meta-wire-workflow-agent.md
.cursor/commands/opsx-meta-refactor-agent.md
.cursor/commands/opsx-meta-review-langgraph.md
.cursor/commands/opsx-meta-debrief-chat.md   (self-improve loop)
opsx/shared/meta-orientation.md              (only if create-vs-fix / routing)
```

Optional with evidence: `opsx-meta.md`, `.cursor/rules/*.mdc` (rule change →
note in plan; fix owner applies).

**Do not** patch product commands or `src/**` from this debrief.

## Wiring trace (Owner «где подключено X»)

Если Owner спрашивает, **где в runtime подключён** агент / hop / design snapshot —
в `SESSION_SUMMARY` или `LESSON` отвечать **цепочкой с путями и строками**, не
абстрактно:

1. **Graph node** (factory + `NODE_ID`) → **execute hop** (`implementation_execute_hop_node`
   или peer для scope).
2. **`_resolve_agent_for_stage`** → **класс агента** (`agents/.../agent.py` +
   line refs из bounded read).
3. **Design snapshot / machine PASS on disk** →
   `opsx-apply-pipeline-identity.mjs` + `design_machine_validation` (и соседние
   validators, если в evidence).

Патч meta-команды из такого урока — только если Owner correction = «команда не
заставила trace сначала» (`ARCH_LAYER: COMMAND_CONTRACT`).

## Procedure

1. Projectization gate (read-only).
2. **Session summary** (3–6 bullets): what was built, which meta commands ran.
3. **Chat archaeology** — list turns where:
   - Owner repeated the same architecture explanation ≥2 times, or
   - Assistant proposed anti-pattern (scatter wiring, `.cursor/agents`, wrong hop), or
   - Owner had to correct boundary (archive Cursor vs LangGraph agent).
4. For each item → `LESSON_ID`, `ARCH_LAYER`, `EVIDENCE` (quote/snippet),
   `ROOT_CAUSE` (which command failed to prevent), `PATCH_TARGET` (file),
   `PROPOSED_GUARD` (concrete text: checklist bullet, anti-pattern, mandatory step).
5. **Dedupe** lessons; merge into `COMMAND_PATCH_PLAN` ordered by impact.
6. **Disposition** (exactly one primary):
   - `PATCH_EXISTING_META_COMMANDS` → fix owner
   - `NEW_SHARED_DOCTRINE` → design-command if new `opsx/shared/*.md` needed
   - `NO_DURABLE_LESSON` → one-off; no patch
   - `NEEDS_MORE_CONTEXT` → ≤2 questions
7. Emit handoff packet (below).

## COMMAND_PATCH_PLAN schema

```text
COMMAND_PATCH_PLAN:
  session_goal: <one line>
  lessons:
    - id: L-001
      arch_layer: LANGGRAPH_WIRING
      evidence: "<short quote or turn ref>"
      root_cause: <which command gap>
      patch_target: .cursor/commands/opsx-meta-wire-workflow-agent.md
      proposed_guard: |
        <exact markdown snippet to add — section name + bullets>
      priority: P0|P1|P2
  shared_surfaces: []   # meta-orientation etc.
  out_of_scope: []      # product / one-off
  verification: |
    How Owner validates next session (e.g. re-run wire intake on same prompt)
```

**P0** = must fix or repeat failure is likely. **P1** = clarity. **P2** = example.

## Output contract

```text
STATUS: DEBRIEF_COMPLETE | BLOCKED_NEEDS_INPUT | FAIL_CLOSED | NO_DURABLE_LESSON
MODE: READ_ONLY | GO_HANDOFF
FOCUS:
SESSION_SUMMARY:
LESSON_COUNT:
COMMAND_PATCH_PLAN: (full block)
DISPOSITION: PATCH_EXISTING_META_COMMANDS | NEW_SHARED_DOCTRINE | NO_DURABLE_LESSON
ROUTING_PACKET:
  AUTHORIZE_META_DEBRIEF_FIX: true   # only when Owner passed GO
  SOURCE_COMMAND: /opsx-meta-debrief-chat
  FIX_SCOPE: .cursor/commands/opsx-meta-*.md + listed shared
  APPLY_ORDER: P0 lessons first; one commit slice per meta-commit GO
WHY:
NEXT_ACTION: /opsx-meta-fix-command-defects | /opsx-meta-design-command | none
DO_NOT_DO_YET:
- edit commands without GO on fix owner
- /opsx-meta-commit (unless separate Owner GO)
ORIENTATION_HINT:
  LANE: META
  NEXT_META: /opsx-meta
  NEXT_ACTION: <same>
```

## After debrief (Owner workflow) — branch on dirty paths

**A. Session changed only `opsx/opsx_runtime/**` (runtime wiring, no command edits):**

```text
1. Read COMMAND_PATCH_PLAN — if lessons are runtime-only → no command patch required
2. NEXT_ACTION: pytest / Owner-local live; `/opsx-commit GO` for product/runtime
3. Do not default to /opsx-meta-fix-command-defects
```

**B. Session changed or debrief targets `.cursor/commands/**` / shared meta:**

```text
1. /opsx-meta-debrief-chat [optional note]
2. Read COMMAND_PATCH_PLAN
3. /opsx-meta-fix-command-defects GO  (paste ROUTING_PACKET; FILES = process only)
4. /opsx-meta-commit GO              (when satisfied)
```

**C. Both runtime and command patches:** runtime commit slice first, then B.

Default **NEXT_ACTION** must match evidence (A vs B). Forbidden: always routing to
fix-command-defects when `COMMAND_PATCH_PLAN` only documents runtime pain.

### Lexicon: SDK (command patches — mandatory)

When `COMMAND_PATCH_PLAN` touches hop/orchestration wording: **`SDK` = Cursor SDK
only** (`cursor_sdk/*`, `GetRun`, stream, **`agent.run` (Cursor SDK transport)**,
`SDK_*` recovery). Never reintroduce loose **SDK hop** / **SDK RCA** for
**OPSX implementation lane** orchestration — **execute hop** / **orchestration RCA**.
SoT: `.cursor/commands/opsx-meta-fix-command-defects.md` § Lexicon: SDK.

## Hard bans

- Auto-edit meta commands on debrief without fix-owner GO
- Commit `.tmp/opsx-chat-audit` (use chat-audit command for registry)
- Blame without evidence
- Expand scope to redesign entire runtime in one debrief

## Anti-patterns

- Treating debrief as full `chat-command-audit` batch walk
- Patching unrelated slash commands without ARCH_LAYER evidence
- Softening colocated wiring doctrine to close debrief faster
- Skipping P0 because «already fixed in code» — commands must encode prevention
- Promoting loose **SDK hop** / **SDK RCA** in command patches when the lesson was
  **implementation lane** orchestration — use **execute hop** / **orchestration RCA**;
  reserve **SDK** for `cursor_sdk/*`, `GetRun`, live-run / `SDK_ACTIVE_RUN_CONFLICT`
- После Owner **«останови все»** / **«не запускай прогон»**: debrief / repair /
  multitask **не** запускают `run_opsx_main.py`, tee в live.log или длинный pytest
  из agent-shell; только текст Owner-local команды в отчёте (без фонового shell).

## Meta chain (close the loop)

```text
review → add → wire → refactor → repair → (work) → debrief → fix meta commands
```

Architecture is **first** in every meta command; debrief **hardens** them from
real chat pain.
