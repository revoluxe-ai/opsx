---
component_id: opsx.commands.meta_add_agent
component_version: 0.1.0
ownership: TARGET_ONLY
runtime_class: ACTIVE_WITH_LIMITS
local_modification_note: "Owner-first intake: free-text need description; agent classifies AGENT_KIND and routes"
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
contract_id: OPSX-WB-META-ADD-AGENT-01
newline_normalization: none
description: "Owner describes the agent/worker needed in plain language; command classifies kind, finds references, asks only when blocked, routes to one meta owner."
argument-hint: "<что нужно — свободным текстом; опционально AGENT:/PURPOSE:/CONTEXT:>"
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

```text
STATUS: FAIL_CLOSED
REASON: TARGET_CAPABILITY_UNRESOLVED
CAPABILITY: <required capability>
NEXT_ACTION: define/discover capability in opsx/project/capabilities.yaml
```

Do not infer product stack from the donor repository.
Control-plane runtime paths under tools/temporal and tools/cursor_executor are
separate from product Temporal/runtime adoption.

# TARGET OVERRIDE — HIGHEST PRIORITY

**Contract:** `OPSX-WB-META-ADD-AGENT-01`
**Runtime class:** `ACTIVE_WITH_LIMITS`
**Precedence:** This override wins for target paths, routing authority, and write boundaries.

```text
IMPLEMENTATION_STATUS: IMPLEMENTED_UNVERIFIED
SOURCE_PARITY: NEW_NATIVE
AUTONOMOUS_DELIVERY_TERMINAL: BLOCKED
NOT_COMMITTED_THIS_RUN: true
```

### Target authority (this command)

- **Reads:** `opsx/projectization.yaml`, `opsx/shared/meta-orientation.md`,
  `opsx/shared/command-discovery.md`, bounded recon under agent/runtime/pipeline
  paths (see below).
- **Writes:** none on the default path (`READ_ONLY` intake and routing only).

### Hard target bans

- Do not install dependencies or modify lockfiles / `package.json`.
- Do not access production / Kubernetes / secrets.
- Do not push, open PR, rebase, force, or deploy.
- Do not create or edit agent/runtime/command files in this invocation.
- Do not invoke `/opsx-meta-commit`, `/opsx-pipeline`, or donor merge here.
- Do not spawn Task/subagents from this command (intake only).
- **Forbidden legacy targets:** `.cursor/agents/**`, custom Task names `opsx-sc-*`,
  fat copies under `opsx-static-check-workers/`.

### Named dependencies

- `.cursor/commands/opsx-execution-preflight.md`
- `.cursor/commands/opsx-meta-add-command.md` (when need is really a slash command)
- `.cursor/commands/opsx-meta-design-command.md`
- `.cursor/commands/opsx-meta-fix-command-defects.md`
- `.cursor/commands/opsx-meta-consult.md`
- `.cursor/skills/opsx-static-check-infra/SKILL.md`
- `opsx/opsx_runtime/agents/repair/README.md`

### Projectization gate

Read `opsx/projectization.yaml` before any downstream recommendation.
If `status` != `COMPLETE` or `normal_commands_enabled` is false:

```text
STATUS: FAIL_CLOSED
REASON: OPSX_PROJECTIZATION_REQUIRED
NEXT_COMMAND: /opsx-projectize
```

---

# /opsx-meta-add-agent

Meta **front door** для создания/расширения «агентов» в смысле OPSX: runtime hop,
pipeline worker, slash-orchestrator, static-check pack, skill или deployed BDK.

**Owner не обязан знать AGENT_KIND, пути и легаси.** Достаточно описать задачу
обычным языком. Команда сама интерпретирует ввод, делает bounded recon,
показывает «как понял», и маршрутизует ровно в **один** downstream owner.

Создание файлов, scaffold и commit — **не** в этом вызове.

## User input

```text
$ARGUMENTS
```

### Минимум

Любой непустой текст после вызова — **достаточно**. Примеры:

```text
/opsx-meta-add-agent
Нужен read-only воркер, который после repair_order проверяет один causal unit
и пишет JSON без перегруппировки findings.
```

```text
/opsx-meta-add-agent
Хочу чтобы bug-team умел отдельным hop маркировать false positive и не трогал OpenSpec.
```

### Опционально (не требовать)

Если Owner сам даёт структуру — используй как подсказки, не как обязательные поля:

```text
AGENT: <короткое имя>
PURPOSE: …
CONTEXT: verify | repair | bug-team | static-check | supervisor | product | deploy
PIPELINE: bug-team | supervisor | verify-graph
SIMILAR_TO: diagnostic | CLASSIFIER | impl_verify | …
NON_GOALS: …
OWNER_CONSTRAINTS: …
```

**Парсинг:** извлеки из свободного текста хотя бы **намерение** (роль, вход/выход,
границы записи, похожий существующий агент). Если после разбора остаются
**блокирующие** неоднозначности — задай **1–3** узких вопроса через `AskQuestion`
(рекомендуемый вариант первым). Не вываливай длинные анкеты и не требуй
заполнить шаблон.

### Lexicon: SDK (Subagent/Task — mandatory)

**`SDK` = Cursor SDK only** in intake, `ROUTING_PACKET`, and downstream briefs:
`cursor_sdk/*`, `GetRun`, stream, **`agent.run` (Cursor SDK transport)**,
`SDK_*` recovery on live Cursor dispatch.

Запрещено в тексте команд: **SDK hop** для plan / `implementation_execute_hop_node` /
`hop_lane_status` / stagnation — только **implementation hop**, **execute hop**,
**mutating hop**, **OPSX implementation lane**; live Cursor —
**mutating hop via Cursor SDK**. SoT:
`.cursor/commands/opsx-meta-fix-command-defects.md` § Lexicon: SDK.

## AGENT_KIND (internal — authoritative for routing)

| AGENT_KIND | Смысл | Где искать референсы |
|------------|-------|----------------------|
| `RUNTIME_VERIFY` | graph hop в verify graph (live: **agent.run** / Cursor SDK transport) | `opsx/opsx_runtime/agents/verify/<peer>/` |
| `RUNTIME_REPAIR` | graph hop в repair graph (live: **mutating hop via Cursor SDK**) | `opsx/opsx_runtime/agents/repair/<peer>/`, `role_contracts.py` |
| `PIPELINE_WORKER` | Роль bug-team / supervisor Task hop | `.cursor/pipeline/bug-team/REGISTRY.md`, `workers/*.md` |
| `CURSOR_ORCHESTRATOR` | Поведение лучше выразить как `/opsx-*` | `.cursor/commands/`, `/opsx-meta-add-command` |
| `STATIC_CHECK_PACK` | Новый check, не отдельный «агент» | `.cursor/static-check/`, skill infra |
| `CURSOR_SKILL` | Только skill без runtime/worker | `.cursor/skills/`, skill `create-skill` |
| `DEPLOYED_BDK` | Хостинговый Agent SDK бот вне репо | `agent-sdk-create-agent` skill |
| `EXTEND_EXISTING` | Доработка уже в `HEAD` | `/opsx-meta-fix-command-defects` |

**Легаси (никогда не предлагать как цель):** `.cursor/agents/**`, `opsx-sc-*`,
универсальный worker (classify+fix+test+close), role-simulation в parent-чате
вместо Task dispatch.

## Intake steps (execute in order)

1. **Normalize** Owner text → рабочие поля `AGENT_CANDIDATE`, `PURPOSE_SUMMARY`,
   `WRITE_BOUNDARY_GUESS`, `SIMILAR_PEERS_GUESS` (внутренне; в отчёте — «КАК ПОНЯЛ»).
2. **Projectization** gate.
3. **Bounded recon** (I/O budget; no full tree dumps):
   - Если похож на verify/repair — glob/grep под
     `opsx/opsx_runtime/agents/**` по ключевым словам и `SIMILAR_TO`.
   - Если похож на bug-team — read `REGISTRY.md` + один ближайший `workers/*.md`.
   - Если похож на orchestrator — grep `.cursor/commands/opsx-*.md` + при необходимости
     `opsx/shared/command-discovery.md` (stale → note `/opsx-meta-update-index`).
   - Если похож на static-check — peek `registry.yaml` + skill infra.
4. **Adjudicate** `AGENT_KIND` + `CREATE vs FIX`:
   - Путь/роль **уже в `HEAD`** и нужны изменения → `ROUTE_CLASS: FIX_COMMITTED`.
   - Новая роль без конфликта → соответствующий `NEW_*`.
   - На самом деле нужна **команда**, не runtime/worker → `REDIRECT_COMMAND`.
   - Deployed bot вне репо → `REDIRECT_BDK`.
   - Нельзя выбрать kind без ответа Owner → `BLOCKED_NEEDS_INPUT` + вопросы.
5. **Route exactly one owner** (never two). **Do not** run the owner in the same turn.

## Create vs Fix (authoritative)

Shared SoT: `opsx/shared/meta-orientation.md` § Create vs Fix.

| Situation | Owner command |
|-----------|---------------|
| Новый runtime/worker/skill (нет в `HEAD`) | Design packet → см. table below |
| Committed agent/worker/command needs change | `/opsx-meta-fix-command-defects` |
| Новая slash-команда | `/opsx-meta-add-command` with derived `COMMAND` + `PURPOSE` |
| Donor import | `/opsx-meta-import-command` only |
| Kind unclear | `/opsx-meta-consult` after questions exhausted |

### Design routing (after this intake)

| AGENT_KIND | `NEXT_ACTION` |
|------------|---------------|
| `RUNTIME_VERIFY` / `RUNTIME_REPAIR` | **`/opsx-meta-design-command`** brief «new runtime agent role» + `REFERENCE_MAP` + tree under `opsx/opsx_runtime/agents/…`; после scaffold → **`/opsx-meta-wire-workflow-agent`** (LangGraph colocated wiring) |
| `PIPELINE_WORKER` | Paste packet for **`/opsx-meta-design-command`** (new worker spec + REGISTRY row) with refs to nearest `workers/*.md` |
| `CURSOR_ORCHESTRATOR` | **`/opsx-meta-add-command`** `COMMAND:` + `PURPOSE:` derived from Owner text |
| `STATIC_CHECK_PACK` | Owner GO on following skill steps; packet cites `opsx-static-check-infra` |
| `CURSOR_SKILL` | Owner GO on **`create-skill`** workflow |
| `DEPLOYED_BDK` | Owner GO on **`agent-sdk-create-agent`** (outside repo) |
| `EXTEND_EXISTING` | **`/opsx-meta-fix-command-defects`** with `AUTHORIZE_COMMAND_FIX` after Owner follow-up |

Explicit: dedicated `/opsx-meta-design-runtime-agent` is **not** in repo yet —
until it lands, runtime/pipeline **create** handoff goes through
`/opsx-meta-design-command` with a structured brief, not silent coding here.

## «КАК ПОНЯЛ» (mandatory before ROUTED)

Короткий блок для Owner на русском:

- что за сущность (runtime / worker / command / check / skill / BDK);
- вход, выход, границы записи;
- 2–5 путей-референсов, которые реально прочитал;
- легаси-предупреждения, если Owner просил «создать агента в `.cursor/agents`».

Если Owner поправит — переклассифицировать до `ROUTED`.

## Model / workers

Intake: **no Task/subagents**. Downstream design: prefer **Composer 2.5**
(`composer-2.5`) explicit; `subagent-model-policy.mdc`; no `*-fast`.

## Output contract

```text
STATUS: ROUTED | BLOCKED_NEEDS_INPUT | FAIL_CLOSED
MODE: READ_ONLY
AGENT_KIND: <taxonomy value | TBD>
AGENT_CANDIDATE: <slug>
PURPOSE_SUMMARY: <one paragraph>
REFERENCE_MAP:
  - <path> — <why>
LEGACY_WARNINGS: []
ROUTE_CLASS: NEW_RUNTIME | NEW_PIPELINE | REDIRECT_COMMAND | REDIRECT_BDK | …
HEAD_PRESENT: yes | no | n/a
SOURCE_PARITY: NEW_NATIVE
AUTONOMOUS_DELIVERY_TERMINAL: BLOCKED
NOT_COMMITTED_THIS_RUN: true
ROUTING_PACKET:
  (pasteable block for NEXT_ACTION owner)
WHY:
NEXT_ACTION: <exact command + args for Owner or agent to run next>
DO_NOT_DO_YET:
- scaffold files in this command
- /opsx-meta-commit
- /opsx-pipeline UPDATE GO
- .cursor/agents/** or opsx-sc-*
ORIENTATION_HINT:
  LANE: META
  STATUS: ROUTED
  NEXT_META: /opsx-meta
  NEXT_ACTION: <same>
```

## Failure / recovery

| Condition | STATUS | NEXT |
|-----------|--------|------|
| Empty input | `BLOCKED_NEEDS_INPUT` | один пример свободного описания |
| Projectization incomplete | `FAIL_CLOSED` | `/opsx-projectize` |
| Command vs runtime vs worker tie | `BLOCKED_NEEDS_INPUT` | AskQuestion (≤3 options) |
| Owner wants legacy `.cursor/agents` | `ROUTED` or `BLOCKED` | explain PIPELINE_WORKER / Task enum; offer redirect |

## LangGraph meta chain

```text
/opsx-meta-review-langgraph       → аудит (READ_ONLY)
/opsx-meta-add-agent              → новая runtime-роль
/opsx-meta-wire-workflow-agent    → узлы + маршруты (colocated)
/opsx-meta-refactor-agent         → рефактор существующей роли / wiring debt
/opsx-meta-repair-langgraph        → сломан workflow / agent / tests
/opsx-meta-debrief-chat           → после сессии: уроки из чата → правки meta-команд
```

Пакет агента должен существовать до wire. Уже в HEAD, но «криво» → refactor, не add.

## Anti-patterns

- Требовать от Owner заполнить AGENT/PURPOSE/CONTEXT шаблоном
- Создавать файлы или «черновик агента» в intake
- Маршрутизировать в два owner
- Универсальный worker «всё в одном»
- `model: inherit` / omit / `*-fast` в handoff without Owner lift
