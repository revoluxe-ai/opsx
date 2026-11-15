---
component_id: opsx.commands.meta_fix_command_defects
component_version: 0.4.0
ownership: EMBEDDED_SHARED
runtime_class: ACTIVE
source_commit: a71601dba3b9f5cd0076ea7185bdbbe56eaf2e3f
source_path: .cursor/commands/opsx-meta-fix-command-defects.md
source_blob_sha: b4aa7e232b2632e72dbdc3bce4006cc50cdc03cb
embedded_canonical_sha256: f02553f3fe064eca1c74425afc49dbcc96d6306f0b80c4b9e911e0a912ab8274
upstream_snapshot: opsx/upstream/a71601dba3b9/.cursor/commands/opsx-admin-command-fix-command-defects.md
required_adapters: ["opsx-lite", "project-generic", "poetry-python"]
local_modification_note: "Projectize 2026-08-07: vue-npm dropped; poetry-python adapter; OPSX-WB-META-SURFACE-01; 2026-10-27 Owner UX — КАК_ПОНЯЛ then GO"
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
contract_id: OPSX-WB-META-SURFACE-01
newline_normalization: none
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

**Contract:** `OPSX-WB-META-SURFACE-01`
**Runtime class:** `ACTIVE`
**Precedence:** This override wins over the canonical source body for target paths, capabilities, safety stops, and authority.

### Target authority paths

- Changes: `openspec/changes/**`
- Policies: `openspec/policies/**`
- Decisions: `openspec/decisions/**`
- Schemas/templates: `openspec/schemas/**`
- Activation/provenance: `opsx_tools/registry/**`
- Frozen upstream: `opsx/upstream/a71601dba3b9/`
- Adapter: `opsx/adapters/poetry-python/README.md`
- Quality gates: `opsx/project/quality-gates.md`

### Hard target bans

- Do not install dependencies / modify `package.json` or lockfile.
- Do not access production / Kubernetes / secrets.
- Do not push, open PR, rebase, force, or deploy.
- Do not activate WB/product domain assumptions from the source body.
- Replace Poetry/pytest/pyright/kubectl execution with capability checks; missing capability => unavailable or FAIL_CLOSED, never PASS-by-absence.
- Mandatory declared executable checks: per `opsx/project/quality-gates.md` (poetry-python). `npm run build` is UNAVAILABLE / not applicable (vue-npm dropped; no root build script).
- Smoke/acceptance/lint/typecheck/frontend tests: UNAVAILABLE unless adapter says otherwise.

### Projectization gate

Read `opsx/projectization.yaml` before any write.
If `status` != `COMPLETE` or `normal_commands_enabled` is false:

```text
STATUS: FAIL_CLOSED
REASON: OPSX_PROJECTIZATION_REQUIRED
NEXT_COMMAND: /opsx-projectize
```

Exempt: `/opsx-projectize` and `/opsx-projectize:GO`.

### ACTIVE allow

Owner unblocked this former-DEFERRED command (2026-08-03): execute canonical source-body semantics **after** remapping paths to target authority above.

Hard bans still apply. Missing Poetry/pytest/pyright/kubectl (and other undeclared source capabilities) => UNAVAILABLE or FAIL_CLOSED — never PASS-by-absence. Do not invent missing adapters or import source product dumps. File/process operations only on target authority paths.

# BEGIN CANONICAL SOURCE BODY
---
name: /opsx-meta-fix-command-defects
id: opsx-meta-fix-command-defects
category: Workflow
description: Understand process-command debt in plain language (READ_ONLY), then fix contracts on GO
argument-hint: "[опиши долг / defect notes] … затем GO"
---

# /opsx-meta-fix-command-defects

## STOP — not this command

If Owner repairs **LangGraph runtime** (`opsx/opsx_runtime/workflows/**`,
`opsx/opsx_runtime/agents/**`, related runtime tests):

- **Do not** invoke or recommend this command.
- Use `/opsx-meta-repair-langgraph` intake, then **apply in chat** on Owner `GO`
  (`DISPOSITION: APPLY_FIX_RUNTIME`).
- If `$ARGUMENTS` denies relevance («не относится», «не эта команда») without a
  process-file defect → `MODE: READ_ONLY` clarification only; **no APPLY**.

**Meta intake vs runtime apply (do not conflate):**

| Symptom class | This command | Runtime apply |
|---------------|--------------|----------------|
| Broken `.cursor/commands/**` contract, STOP text, agent confusion | **Yes** (process `FILES` only) | No |
| Slice # next to **implementation procedure** name (`procedure=`, `cli_outcome`, graph logs) | **No** | `routing_contract`, `cli_outcome`, `code_pipeline/graph.py` |
| Owner «рядом с apply_scaffold» without **задокументируй команду** | **No** — not agent.py meta links | repair-langgraph intake → `APPLY_FIX_RUNTIME` |

**Forbidden APPLY misread:** Subagent/Task must **not** patch
`opsx/opsx_runtime/agents/**/agent.py` module docstrings with `/opsx-meta-*` slashes
to satisfy «рядом с hop» / slice next to procedure — that is runtime wiring or
CLI label work, not command-defects unless Owner explicitly asked to document the
slash in **process** files listed in `FILES:`.

## Lexicon: SDK (mandatory guard — process commands)

Agents and Subagent/Task briefs **must not** use loose **«SDK»** for OPSX
implementation orchestration. **`SDK` in meta commands means Cursor SDK only**
unless a row in the table below says otherwise.

| Term | Means | Use instead for OPSX implementation lane |
|------|--------|-------------------------------------------|
| **Cursor SDK** / **SDK (allowed)** | `agents/adapters/cursor_sdk/*`, transport, `GetRun`, stream, `run.wait`, **`agent.run` (Cursor SDK transport)** / **mutating hop via Cursor SDK**; recovery codes such as `SDK_ACTIVE_RUN_CONFLICT` tied to **live Cursor runs** / dispatch ledger reconcile | — |
| **Forbidden loose «SDK»** | Calling plan hop, super-implement, `implementation_execute_hop_node`, `hop_lane_status`, stagnation, or mutating apply lane **«SDK hop»** / **«SDK RCA»** | **implementation hop**, **mutating hop**, **OPSX implementation lane**, **execute hop** |

**Forbidden phrasing in command bodies, Subagent/Task, and examples:**

- «SDK hop» for `implementation_execute_hop_node` (unless the sentence is **only**
  about invoking the Cursor adapter / `agent.run` transport).
- «SDK RCA» when the fix is `hop_lane_status`, plan/receipt, or machine gates in
  `workflows/**` — say **orchestration RCA** first (repair L-006).
- «LangGraph SDK agent» as a synonym for the whole OPSX agent package — say
  **OPSX LangGraph runtime agent**; Cursor SDK is the **adapter transport** slice only.

When patching `.cursor/commands/**`, normalize stale loose **SDK** wording to the
table above; keep `cursor_sdk/*`, `GetRun`, `SDK_ACTIVE_RUN_CONFLICT`, and
`SDK_*` recovery codes explicitly **Cursor SDK**.

---

Plan or apply confirmed command-contract defects on **process contracts only**
(`.cursor/commands/**`, `.cursor/rules/**`, `.cursor/skills/**`,
`.cursor/agents/**`, `.cursor/static-check/**`, `.agents/**`, related shared
OPSX contracts, and mirrors when listed).

This command never edits production code (`src/stroy/**`, `app/**`), product
tests (`tests/**` outside listed control-plane harnesses), migrations,
product specifications, or product tasks. An input packet cannot expand
this boundary **unless** the Owner explicitly authorizes the stroy-system
control-plane expansion below in the same chat as `GO` (or legacy
`AUTHORIZE_COMMAND_FIX`).

## Простой цикл Owner (primary UX)

1. **Вызов** — `/opsx-meta-fix-command-defects` и обычным текстом: в чём долг
   (какая команда/правило, что не так, чего хочешь). Пакет `FIX_PLAN_READY`,
   handoff из `/opsx-tech-debt` или review — **опциональны**, не обязательны.
2. **Осмысление (READ_ONLY)** — агент читает evidence, при необходимости
   узко открывает затронутые process-файлы, затем отвечает блоком
   **КАК_ПОНЯЛ** (см. Output): симптом, затронутая команда, список `FILES`,
   предлагаемые правки, один уточняющий вопрос если неясно. Owner может
   поправить понимание в следующем сообщении без `GO`.
3. **GO** — Owner пишет точный токен `GO` (в том же чате). Агент правит
   **только** `FILES` из последнего согласованного **КАК_ПОНЯЛ** / плана в
   этом чате для этой команды; повторно вставлять пакет не требуется.
4. **Diff review** — `READY_FOR_COMMAND_DIFF_REVIEW`; commit/push отдельно Owner.

Если неясно, defect это или redesign → `/opsx-meta-consult` сначала.

### Owner-expanded APPROVED scope (stroy-system control-plane)

When the Owner understanding includes **both** `GO` (or legacy
`AUTHORIZE_COMMAND_FIX`) and an
explicit expansion authorization (e.g. mandate naming control-plane runtime
repair), APPLY may edit **only** paths listed in that packet’s `FILES:` /
`APPROVED_FILES` within:

| Allow (bounded packet `FILES:`) | Deny (always) |
|---------------------------------|---------------|
| `.cursor/commands/**`, `.cursor/rules/**`, `.cursor/skills/**`, `.cursor/agents/**`, `.cursor/static-check/**`, `.agents/**` | `src/stroy/**`, `app/**`, deploy/k8s, secrets |
| `.cursor/pipeline/**`, `opsx_tools/**` | Product OpenSpec tasks/specs unless listed as process mirror only |
| `tools/temporal/**`, `tools/cursor_executor/**` | Model tier / `agent-models.json` / SDK pin changes unless explicitly in `FILES:` |
| Related OPSX process contracts, schemas, and **hermetic** tests for the above | Arbitrary repo writes; live Temporal/Postgres restart; worker redeploy |

**Gates (unchanged):** work list from **КАК_ПОНЯЛ** / plan `FILES:` only;
evidence-backed understanding; no false PASS-by-absence; post-APPLY diff
review; no stage/commit/push from this command. Expansion does **not** substitute
`/opsx-apply` for product semantics — runtime edits require the expanded
packet to name each runtime path explicitly.

This command never stages, commits, pushes, or invokes a commit command.
Commits are performed separately by the owner using the repository's
dedicated commit command.

If the human is still unsure whether the issue is a contract defect, a
pipeline redesign topic, a new-command need, or only a relationship question
→ `/opsx-meta-consult` first. Use this command when you can describe the
defect in plain language (packets and PTD handoffs are optional evidence).

Follow `.cursor/commands/opsx-source-resolution.md` and
`.cursor/commands/opsx-execution-preflight.md` (including **Read / tool I/O budgets**).

## User input

```text
$ARGUMENTS
```

Argument is **optional**. Invoking the command with an empty argument is valid.

## Modes

| Input | Mode | Writes |
|---|---|---|
| no apply token (default) | `READ_ONLY` | never |
| exact token `GO` (primary) **or** legacy `AUTHORIZE_COMMAND_FIX` **or** same-message fix intent (see below) + usable **КАК_ПОНЯЛ** / plan | `APPLY` | `FILES` from last **КАК_ПОНЯЛ** / plan in this chat only |

### Same-turn fix intent (mandatory — no second Owner slash)

APPLY is also authorized when the user message **both**:

1. invokes `/opsx-meta-fix-command-defects` (this command as PRIMARY), and  
2. contains fix intent in natural language (non-exhaustive: `исправь`, `fix`, `почини`, `apply`, `сделай`, `implement`, «разберись», «исправь эту проблему»),

**or** the immediately preceding assistant output in this chat is **КАК_ПОНЯЛ** / `STATUS: FIX_PLAN_READY` for this defect and the user replies with `GO`, `AUTHORIZE_COMMAND_FIX`, or fix intent.

In those cases the agent MUST enter APPLY in **the same turn** and MUST NOT ask Owner to paste `AUTHORIZE_COMMAND_FIX` or re-run this slash.

`GO` is the primary apply token. `AUTHORIZE_COMMAND_FIX` is a legacy alias.
Both authorize command-contract edits only.
They are not interchangeable with feature-command tokens such as
`AUTHORIZE_PROTECTED_INFRA_EDIT`, `AUTHORIZE_DESIGN_IMPLEMENTATION`, or
`AUTHORIZE_ONE_LIVE_RUN`. They do not authorize live/destructive tests.

### READ_ONLY (default — no mandatory argument)

When apply tokens are absent:

- do not edit files;
- do not stage, commit, or push;
- do not require a pasted `FIX_PLAN_READY` packet;
- build or refine a fix plan from whatever is available:
  1. optional `$ARGUMENTS` / pasted packet;
  2. else the latest completed review / analyze output in this chat
     (`CONFIRMED_DEFECTS`, `FIX_PLAN_READY`, or equivalent);
  3. else a short free-form defect note in `$ARGUMENTS` or the latest user
     message.
- **Pipeline-stuck shortcut:** если в `$ARGUMENTS` или последнем Owner-сообщении
  только «чего встало» / «застряло» / «без заебов» без defect packet — используй
  summary из этого чата (split-apply, verify, scenario audit, последний
  `BLOCKED_*` / pytest error) как evidence; не останавливайся на уточняющем
  вопросе, если хотя бы один blocker уже назван в thread.
- If none of those exist **and** no pipeline-stuck evidence in thread → ask **one**
  narrow question for the defect source (review output, file list, or short
  description). Do not invent defects.
- Lead with human-readable **КАК_ПОНЯЛ** (see Output). Optionally append a
  compact machine `FIX_PLAN_READY` tail for handoff/automation — not the
  primary Owner-facing surface (or `BLOCKED_NEEDS_EVIDENCE` if evidence is
  insufficient).
- If same-turn fix intent applies (see Modes) → proceed to APPLY in this turn;
  do not stop at READ_ONLY.
- Else end with `NEXT_ACTION: Reply: GO` to apply. Do **not** require Owner to run shell.
- Print `OWNER_RUN_COMMAND` **only** when Owner explicitly asked for copy-paste /
  exact CLI text (never as default).

### APPLY (requires `GO`, legacy `AUTHORIZE_COMMAND_FIX`, or same-turn fix intent)

You may edit files **only** when same-turn fix intent applies (see Modes)
**or** user input contains the exact token `GO` **or** `AUTHORIZE_COMMAND_FIX`
**and** the immediately preceding **КАК_ПОНЯЛ** / approved plan in **this
chat** lists non-empty `FILES` and `PROPOSED_BEHAVIOR` (formal
`STATUS: FIX_PLAN_READY` packet is optional if understanding is present).

If an apply token is present but there is no usable understanding/plan → stop
with `BLOCKED_NEEDS_FIX_PLAN` (still no writes).

## Packet shape (optional — handoff / automation)

Formal packets remain valid evidence. Owner-facing default is **КАК_ПОНЯЛ**.

```text
КАК_ПОНЯЛ:
  симптом:
  команда:
  FILES:
  что_поменяем:
  вопрос_owner: (optional)

STATUS: FIX_PLAN_READY   # optional tail
DEFECTS_TO_FIX:
...
FILES:
...
PROPOSED_BEHAVIOR:
...
```

For APPLY: empty `FILES` or missing proposed behavior in the bound
understanding/plan → `BLOCKED_NEEDS_FIX_PLAN`.

## Hard I/O rules (mandatory — hang prevention)

Do **not** reproduce the failure mode of bulk parallel Reads.

1. Build the work list **only** from `FILES:` in the bound **КАК_ПОНЯЛ** /
   plan (or `APPROVED_FILES` in a formal packet), plus shared contracts those
   FILES already cite by exact path. In READ_ONLY, derive a provisional file
   list from cited paths in the chat — never from a full command-set dump.
2. Never open `opsx-meta-analyze-pipeline.md`, `opsx-learn-from-bug.md`,
   `opsx-meta-design-command.md`, or other large siblings unless they
   appear in `FILES:` or are an exact cited dependency required for the current
   defect.
3. Process **one defect at a time**, and within a defect **one file at a time**:
   - `Read` that exact path (use `limit`/`offset` or Grep-first for files > ~200 lines);
   - in APPLY: patch for `PROPOSED_BEHAVIOR`; in READ_ONLY: only inspect;
   - only then open the next file.
4. Max **2** concurrent `Read` calls — prefer **1** for this command.
5. Prefer `Grep` over full-file Read when locating a token.
6. No Task/subagent “extract all opsx commands”. Fix/plan from packet or chat
   evidence only.
7. If a Read stalls/times out → stop stacking Reads; report
   `blocked_preflight_timeout` for that path; continue other listed files only
   if independent.

## Steps

### READ_ONLY

1. Resolve evidence source (optional args → chat packet → free-form note).
2. Parse or draft `DEFECTS_TO_FIX` + `FILES` + `PROPOSED_BEHAVIOR`.
3. Optionally spot-check listed files (narrow Grep/Read) to validate the plan;
   do not write.
4. Return **КАК_ПОНЯЛ** (+ optional `FIX_PLAN_READY` tail) or blocked status;
   `NEXT_ACTION: Reply: GO`.

### APPLY

1. Bind to the last **КАК_ПОНЯЛ** / plan in this chat. Parse defects into an
   ordered list. Announce count
   and the exact file set (deduped). Edit **only** listed exact process paths.
2. For each defect:
   - Restate `CURRENT_BEHAVIOR` → `PROPOSED_BEHAVIOR` in one short line.
   - For each path in that defect’s `FILES:` / `APPROVED_FILES`:
     - resolve exact path (no rediscovery);
     - Grep/Read narrowly;
     - patch to match proposed behavior;
     - skip if already compliant (record `already_fixed`).
3. After all listed files: quick self-check that no new unbounded
   “read all / search all decisions” instruction was introduced.
4. Do **not** stage, commit, push, or invoke any commit command.
5. Enter **Post-APPLY diff review gate** (mandatory) and stop.

## Post-APPLY diff review gate (mandatory)

After successful APPLY edits:

1. This command never stages, commits, pushes, or invokes a commit command.
2. Return:

   ```text
   STATUS: READY_FOR_COMMAND_DIFF_REVIEW
   ```

3. Must show:
   - `git status --short`
   - list of changed files
   - full diff of **ONLY** approved FILES from the fix packet
   - simulation results (static/dry-run matrix for touched command contracts)
   - confirmation: no changes outside approved FILES
   - confirmation: no commit / push / stage performed
4. Stop.

Final `NEXT_ACTION`:

```text
Review the diff. Commit is performed separately by the owner using the
repository's dedicated commit command.
```

Do **not** print a commit command automatically.
Do **not** accept, wait for, or manage commit-authorization tokens.

## Out of scope

- Production / app code, product tests, migrations, product specs, product tasks
  (packet cannot expand this boundary)
- `openspec/decisions/**` content (unless listed in FILES as a process mirror —
  still never product tasks)
- Running `/opsx-review-commands` or full command-set re-audit mid-fix
- “While I’m here” refactors of unrelated commands
- Any write while apply tokens are absent
- Feature implementation / unit / integration / live / destructive project tests
- Docker, real PG/CH, network/API as part of this command
- Staging, committing, pushing, or invoking any commit command

## Output

### READ_ONLY

Lead with **КАК_ПОНЯЛ** (plain language, Russian OK for Owner-facing text):

```text
MODE: READ_ONLY

КАК_ПОНЯЛ:
- симптом: ...
- команда / контракт: ...
- FILES:
  - ...
- что поменяем на бумаге: ...
- вопрос: ...   # omit if clear

STATUS: FIX_PLAN_READY | BLOCKED_NEEDS_EVIDENCE | blocked_preflight_timeout
# optional compact tail — DEFECTS_TO_FIX / FILES / PROPOSED_BEHAVIOR

READ_IO:
- paths_opened:
- parallel_max_used:
- timeouts:

NEXT_ACTION:
- Reply: GO
  (legacy AUTHORIZE_COMMAND_FIX still accepted; or provide missing evidence if blocked)

OWNER_RUN_COMMAND:
```

```text
/opsx-meta-fix-command-defects GO
```

When the owner asks for «точные команды» / copy-paste:

1. Print the fenced `OWNER_RUN_COMMAND` (`GO` only — no packet paste required).
2. Do not require `APPLY_INPUT_PACKET`; the prior **КАК_ПОНЯЛ** in-chat is the plan.

### APPLY (after edits — stop here)

```text
STATUS: READY_FOR_COMMAND_DIFF_REVIEW | PARTIAL | BLOCKED_NEEDS_FIX_PLAN | blocked_preflight_timeout
MODE: APPLY

DEFECTS_APPLIED:
- id/title:
  - files_changed:
  - already_fixed:
  - skipped_reason:

CHANGED_FILES:
- <exact path>
OUTSIDE_APPROVED_FILES: none | <list>

SIMULATION_RESULTS:
- <matrix rows>

STAGE_COMMIT_PUSH: not_performed
TESTS_LIVE_DOCKER_NETWORK: not_run

READ_IO:
- paths_opened:
- parallel_max_used:
- timeouts:

FILES_NOT_TOUCHED:
- (explicit: production, product tests, migrations, specs, tasks, unlisted commands)

NEXT_ACTION:
Review the diff. Commit is performed separately by the owner using the
repository's dedicated commit command.

(optional later, transferable process class?): /opsx-meta-promote-process-lesson
  (separate from product /opsx-learn-from-bug; never auto-print a commit command)
```

After a successful APPLY that reaches `READY_FOR_COMMAND_DIFF_REVIEW`, if the
defect class looks transferable (missing guardrail, wrong branch,
durable-handoff omission, ADL/exhaust gap, etc.), prefer hinting
`/opsx-meta-promote-process-lesson` as an optional later step —
do not mix into the forced stop before diff review, and do not print a
commit command.

## Self-review

Before returning:

- mode matches input (`GO` / legacy `AUTHORIZE_COMMAND_FIX` ⇒ APPLY, else READ_ONLY);
- READ_ONLY led with **КАК_ПОНЯЛ** and never wrote files;
- every APPLY edit maps to bound **КАК_ПОНЯЛ** / plan defect + listed process file;
- no production / product-test / migration / product-spec / product-task edits;
- no bulk preload of the command set;
- decision lookup changes stay context-first / bounded fallback;
- I/O stayed within execution-preflight Read budgets;
- APPLY ends at `READY_FOR_COMMAND_DIFF_REVIEW` with no stage/commit/push and
  no commit-command handoff;
- when owner asked for copy-paste/exact commands in READ_ONLY:
  `OWNER_RUN_COMMAND` for apply token is printed (never a commit command).
- Lexicon: SDK guard present where defect class is agent/hop confusion; no new
  loose **SDK hop** / **SDK RCA** phrasing in touched command text.
# END CANONICAL SOURCE BODY

# TARGET ADAPTATION NOTES

- upstream_snapshot: `opsx/upstream/a71601dba3b9/.cursor/commands/opsx-admin-command-fix-command-defects.md`
- runtime_class: `ACTIVE`
- active_name: `/opsx-meta-fix-command-defects`
- openspec_store: `openspec/changes/**` (not source multi-store / not `opsx/changes` SoT)
- quality: poetry-python gates per `opsx/project/quality-gates.md` when in scope; `npm run build` UNAVAILABLE; smoke/frontend UNAVAILABLE
- unresolved_source_sections: any Poetry/pytest/pyright/kubectl/WB-domain steps in the body are blocked by TARGET OVERRIDE until adapters exist
- manual_review: `opsx/project/manual_review.md`
- central_discovery: when identity is missing/uncertain or a central replacement
  may close the defect, compare BASE/LOCAL/CENTRAL using a fresh index. Stale
  negative → `BLOCKED_NEEDS_FRESH_INDEX`. Valid central fix → hand off to
  `/opsx-meta-import-command`. Do not merge command bodies in this command.
- orientation: emit `ORIENTATION_HINT` with `NEXT_META: /opsx-meta`
- process lesson handoff: `/opsx-meta-promote-process-lesson`## Speckit hook parity sweep (mandatory when in scope)
When the defect or evidence touches `**/speckit-*/SKILL.md` and Spec Kit
`HookExecutor` / hook `condition` / `extension` binding semantics:
**Trigger:** any cited path under `speckit-*/SKILL.md` plus hook-related
tokens (`HookExecutor`, `condition`, `extension`, `EXECUTE_COMMAND`,
`compare string forms`).
**Canonical reference:** `.agents/skills/speckit-analyze/SKILL.md` (or an
already-compliant sibling named in the packet).
**Budget:** grep/spot-check **only** `**/speckit-*/SKILL.md` under `.agents/`
and `.cursor/` — never a full command-set dump.
### READ_ONLY (plan expansion)
Before returning `FIX_PLAN_READY`, run bounded grep:
1. `compare string forms` under `**/speckit-*/SKILL.md` — every hit is a
   stale hook block;
2. files missing `Resolve the hook entry's \`extension\` field` among the same
   paths when the defect is HookExecutor-related.
Every hit MUST appear in `FILES:` for both `.agents` and `.cursor` mirrors.
Do not close the plan on a single cited skill when sibling hits remain.
### APPLY (verification)
After patching all listed `FILES:`:
1. Re-run the same bounded greps;
2. If any stale hit remains → `STATUS: PARTIAL` (not
   `READY_FOR_COMMAND_DIFF_REVIEW`); list remaining paths in output;
3. Each patched file must update **all** hook sections in that file
   (`before_*` and `after_*` keys).
   When in scope, apply **Speckit hook parity sweep** (READ_ONLY) to expand
   `FILES` before step 4.
3. When in scope, run **Speckit hook parity sweep** (APPLY verification);
   stale sibling hits ⇒ `PARTIAL`, not `READY_FOR_COMMAND_DIFF_REVIEW`.
4. After all listed files: quick self-check that no new unbounded
5. Do **not** stage, commit, push, or invoke any commit command.
6. Enter **Post-APPLY diff review gate** (mandatory) and stop.
- when Speckit hook parity was in scope: no stale `compare string forms` /
  missing `extension` binding remains under `**/speckit-*/SKILL.md`;
