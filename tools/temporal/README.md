# Temporal Review/Repair Loop POC

Isolated proof-of-concept that orchestrates Owner Cursor Agent cycles with
separate prompt invocations. **Three lanes share one worker and one task queue.**
Do not start a second worker on `cursor-bugbot-repair-poc`.

**One live PP per lane.** Workflow IDs are stable (`{prefix}-{mode}` plus `change`
for hunt/verification). A second start while the first is RUNNING is rejected
(`STATUS: BLOCKED`, `REASON: PIPELINE_ALREADY_RUNNING`). After COMPLETED the
same ID may run again. Operator `run` also takes a process lock so a retry
does not spawn a second `start.py`.

### Lane: bugbot (default)

```text
/review-bugbot against stage (no Task / no subagents)
→ wait for terminal completion
/opsx-orch-bug-fix
→ wait for terminal completion
/review-bugbot against stage
→ ...
```

### Lane: security

Cursor `/review-security` skill (not `/opsx-security-review`). Repair is
`/opsx-bug-team` on product-lane rows from the immediate prior review table
(not `/opsx-orch-bug-fix` — wrong R2).

```text
/review-security  (src/tests scope; POC git preflight artifact; Task
                   subagent_type=security-review; FALLBACK from artifact if
                   subagent cannot compute branch-diff)
→ wait for terminal completion
/opsx-bug-team  (src/tests intake from prior table; process rows excluded)
→ wait for terminal completion
/review-security
→ ...
```

### Lane: project-bug-hunt

```text
/opsx-project-bug-hunt base=<base> head=<head> change=<change>
→ wait for HUNT_COMPLETE
/opsx-orch-bug-fix change=<change>
→ wait for BUG_BATCH_CLEAR (or stop on REPAIRED_THIS_BATCH=NO)
/opsx-project-bug-hunt ...
→ ...
```

Stop when `findings_total=0` (`HUNT_CLEAN`) or orch finishes without repair
(`NO_REPAIR_PROGRESS`). Tokens are CLI flags (`--base`, `--head`, `--change`).

**Heartbeat (project-bug-hunt activities):** `execute_project_bug_hunt` and
`execute_project_bug_hunt_orch` run the Cursor SDK hop in a worker thread and
call `activity.heartbeat` every `PROJECT_BUG_HUNT_HEARTBEAT_INTERVAL_SECONDS`
(30s). `ProjectBugHuntWorkflow` uses `hunt_start_to_close_timeout()` (1h) and
`hunt_heartbeat_timeout()` (15m) from `session_lifecycle_contract.py`. A long
medium-review / low-repair orch hop is expected to exceed 15 minutes wall-clock;
without heartbeats Temporal fails the activity with `TIMEOUT_TYPE_HEARTBEAT`.
Raising `heartbeat_timeout` alone does not replace heartbeats.

Default Docker bridge networking on Cloud VMs can block Temporal from reaching the compose `postgresql` service and block **temporal-ui** from reaching `temporal:7233`. On Cursor Cloud Agent, **`STROY_TEMPORAL_CLOUD_COMPOSE` defaults to on** (set `=0` to disable). You can also set **`STROY_TEMPORAL_CLOUD_COMPOSE=1`** explicitly. The operator then runs:

```bash
python start.py --mode show-models
```

(`sudo` prefix when needed.) The cloud override publishes Postgres on host port **5435**, points Temporal at `host.docker.internal:5435`, and points **temporal-ui** at `host.docker.internal:7233` (override with `STROY_TEMPORAL_UI_GRPC_HOST`).

**Browser on Cloud Project Desktop:** open `http://localhost:8233` on the VM desktop — not Owner Windows localhost. See `docs/opsx/cloud-temporal-monitoring.md` § Browser access. The same bridge limitation breaks `temporal-ui` → `temporal:7233`; the override points UI at `host.docker.internal:7233` (published gRPC on the host).

Manual equivalent from `tools/temporal`:

```bash
python worker.py --mode real --repo C:/Users/vkita/my_code/game --model-config agent-models.json
python start.py --mode temporal-real --repo C:/Users/vkita/my_code/game --model-config agent-models.json
```

### Owner monitoring on Cursor Cloud Project (no SSH)

Owner **cannot** use Windows `http://localhost:8233` to watch a hunt on a Cloud Agent VM. Use chat `STATUS` blocks, the Cursor agent run page, and JSON manifests instead of exposing Temporal UI publicly.

| What | Where |
|------|--------|
| Playbook | `docs/opsx/cloud-temporal-monitoring.md` |
| Live manifest (repo) | `opsx/runs/temporal/monitoring/latest.json` |
| Artifact mirror (uploaded) | `/opt/cursor/artifacts/temporal-pipeline-monitoring.json` |
| Workflow status dump | `opsx/runs/temporal/runs/<workflow-id>.json` |

`tempo_operator.py` writes the manifest at pipeline start, when `WORKFLOW_ID` is emitted, on completion, and on `runtime.py status --workflow-id …`. `TEMPORAL_UI` in Owner reports remains **VM loopback reference only**.

## Machine checks

```bash
python worker.py --mode real --repo C:/Users/vkita/my_code/game --review-tier low --repair-tier low
python start.py --mode temporal-real --lane project-bug-hunt \
  --repo C:/Users/vkita/my_code/game --review-tier low --repair-tier low
```

## /tempo-* operator commands

Shared kernel: `.cursor/commands/_shared/tempo-pipeline.md`  
Process helper: `python tools/temporal/tempo_operator.py`  
Pipeline specs: `tools/temporal/pipelines.json` (one command = one pipeline)

| Command | What it runs |
|---------|----------------|
| `/tempo-bugbot [base=stage] <review> <repair>` | review-bugbot → orch until `BUGBOT_CLEAN`; `head` = current branch |
| `/tempo-security [base=stage] <review> <repair>` | review-security → bug-team until `SECURITY_CLEAN`; `head` = current branch |
| `/tempo-project-bug-hunt change=<id> [base=stage] <review> <repair>` | hunt → orch until `HUNT_CLEAN`; `head` = current branch |
| `/tempo-verification change=<id> <next-check> <executor>` | next-check → assigned executor until `ALL_REQUIRED_CHECKS_PASS`; FAIL/BLOCKED stop. No Owner GO/focus pause. |

Example: `/tempo-bugbot low low`

Example: `/tempo-security low low`

The operator contract keeps two positional tier arguments: review, then repair.
For a review ladder, run separate invocations such as `low low`, `medium low`,
and `high low`; do not combine the tiers into an arrow token.

Example: `/tempo-project-bug-hunt change=cross-device-novel-save-refresh low low`

Example: `/tempo-verification change=cross-device-novel-save-refresh low low`

### Cloud browser handoff (Tempo ↔ parent agent)

On Cloud Agent VMs nested `agent` hops lack `cursor-ide-browser`. Tempo runs
until `smoke_acceptance`; the **parent chat agent** runs `/opsx-browser-check`,
writes bounded results, reconciles the ledger, then Tempo resumes for
`security_review` + `frontend_build`. No Owner GO, no CDP fallbacks in Tempo.

Protocol: `tools/temporal/CLOUD-BROWSER-HANDOFF.md` · `AGENTS.md`

### Post-completion: owner-done-signal

When a `/tempo-*` pipeline reaches a terminal workflow outcome, configured
`post_completion_steps` run from `tempo_operator.py`. Verification declares
`owner-done-signal`: a static HTML page
(`tools/temporal/assets/owner-done-signal.html`) opened in Owner Chrome via
**user-chrome-devtools** `navigate_page` on the existing `--autoConnect`
session. Manifest: `opsx/runs/temporal/gates/owner-done-signal/<workflow_id>.json`.

### Pre-assignment: owner-chrome-focus-gate (RETIRED)

Owner GO / Chrome-focus pause is **retired**. `/tempo-verification` starts
`smoke_acceptance` / `opsx-browser-check` without waiting for chat `GO` or a
focus ack. `evaluate_pre_run_gate` always returns `None`. Historical CLI
`owner-chrome-focus-ack` / `owner-browser-check-go` remain for compatibility
and do not gate the live loop.

Ack path (unused by live scheduling):
`opsx/runs/temporal/gates/owner-chrome-focus-gate/acks/<check>_epoch<N>.json`

### Verification sequencing

`VerificationExecutionWorkflow` is scheduler → executor → scheduler. Two
process rules:

1. **Heartbeat** — real `execute_next_check` / `execute_assigned_check` hop
   `agent` CLI in a thread and heartbeat every 30s. Without this, Temporal
   kills a live hop at `heartbeat_timeout` (15 min next-check / 30 min executor).
2. **EVIDENCE_MISSING** — `/opsx-next-check` writes `EVIDENCE_MISSING` when
   assigning the *next* frozen check (no result file yet). That is not a stop.
   Stop only when the gap is for the same `required_check_id` already executed
   (`should_stop_on_evidence_gap`). `executor_runs > 0` is not sufficient.

Owner runs **only** the slash command. Agent calls:

```bash
python tools/temporal/tempo_operator.py run bugbot --repo <repo> \
  --review low --repair low

python tools/temporal/tempo_operator.py run project-bug-hunt --repo <repo> \
  --review low --repair low --change <id>

python tools/temporal/tempo_operator.py run verification --repo <repo> \
  --review low --repair low --change <id>
```

## Review contract

`REVIEW_PROMPT` in `config.py` enforces:

- full branch diff `stage...HEAD` (not working-tree-only)
- one Shell command per invocation (no `&&`)
- ignore stale hook/Smart Mode narratives from prior turns
- required header: `REVIEW_SCOPE: FULL_BRANCH_DIFF`

Parser rejects degraded/partial reviews (missing scope, working-tree-only, shell-blocked narratives) as `REVIEW_FAILED` so Temporal retries.

## Repair contract

`REPAIR_PROMPT` in `config.py` still starts with `/opsx-orch-bug-fix` (R2 on
the same chat as the successful review). Extra POC lines tell the agent:

- Shell is available; 1Password skip/allow is not a deny
- one Shell command per call (no `&&`); never `npm test`
- after patch, GREEN via `node --test tests/unit/<family>.test.mjs`
- `BLOCKED_WORKER_STATE` only on a real Shell deny / node failed to start

`SECURITY_REPAIR_PROMPT` starts with `/opsx-bug-team` (R2 on the prior
`/review-security` table). Process-only findings stop the loop as
`SECURITY_CLEAN` before bug-team runs. Terminal token: `STATUS: SECURITY_BATCH_CLEAR`.

## Fresh review sessions

To avoid stale hook/shell context in `--resume` chats:

- **review retry** (transient or quality failure) → `reset_session()` → new `create-chat`
- **post-repair re-review** → fresh chat before the next review
- **repair** stays on the same chat as the successful review (bugbot R2 / security table)

Up to `REVIEW_TECHNICAL_RETRY_MAX + 1` review attempts per round (default: 3).

## Git preflight (real mode)

Before each review, POC runs local git outside the Cursor agent:

```text
git rev-parse --verify stage
git merge-base stage HEAD
git diff --stat stage...HEAD
git diff stage...HEAD -- <changed files>
```

The diff is injected into the review prompt as a **compact summary** plus an on-disk artifact (`.tmp/temporal/git-preflight-latest.md`) that the agent opens via Read. This avoids Windows `WinError 206` from oversized CLI argv.
The agent does not need Shell for git; hook/shell narratives are ignored when preflight succeeded.

Security lane uses the **same** injection, scoped to `src/**` + `tests/**`
(`REVIEW_SCOPE: SRC_TESTS_ONLY`). When `POC_GIT_PREFLIGHT: VERIFIED` is present,
the review agent must Read `PREFLIGHT_ARTIFACT` first, keep `Base Branch` as the
operator `--base` (default `stage`, never `origin/HEAD` / master), and FALLBACK
to summarizing from the artifact if the `security-review` subagent cannot
compute branch-changes (e.g. deleted remote tracking). Pure subagent blocker
text without a summary phrase/table is still incomplete.

## Product vs process finding filter

Source of truth: `finding_filter.py` (aligned with `opsx-bug-intake-clear` Pass 0.25).

| Path prefix | Lane | Automation |
|-------------|------|------------|
| `src/**`, `tests/**`, `backend/**`, `openspec/changes/**` | game product | repair |
| `tools/**` (including `tools/temporal/**`) | tooling / this POC | excluded |
| `.cursor/**`, `.agents/**`, `opsx/**`, `opsx-knowledge/**`, `.tmp/opsx*` | process | excluded |
| anything else (`package.json`, `forum/**`, …) | out of loop | excluded |

Allowlist is the gate (`finding_filter.PRODUCT_LOCATION_PREFIXES`). Process prefixes are documentation; they are not required for exclusion.

If only non-product findings remain → `BUGBOT_CLEAN` / `SECURITY_CLEAN`, loop stops without repair.

Security lane uses the **same** product/process filter. Control-plane hits under
`opsx/**` are excluded from automation repair (no invented `/opsx-security-fix`).

## Security vs Bugbot vs project-bug-hunt (do not mix)

| | bugbot | security | project-bug-hunt |
|--|--------|----------|------------------|
| Review | `/review-bugbot`, no Task | `/review-security` skill, one `security-review` subagent | `/opsx-project-bug-hunt base=… head=… change=…` |
| Git | Python preflight injected | Scoped src/tests preflight injected; Read artifact + Base Branch; FALLBACK if subagent branch-diff fails | Hunt snapshot tool; no bugbot preflight |
| Repair | `/opsx-orch-bug-fix` | `/opsx-bug-team` | `/opsx-orch-bug-fix change=…` |
| Clean status | `BUGBOT_CLEAN` | `SECURITY_CLEAN` | `HUNT_CLEAN` |
| Extra stop | — | — | `NO_REPAIR_PROGRESS` only when no durable repair evidence (product worktree mutation or new `closed_fixed` materialize receipts) **and** prose says no repair (`REPAIRED_THIS_BATCH=NO` / `FIX_NOW: (none)`); defer-only batches still stop |
| Workflow type | `BugbotRepairWorkflow` | `SecurityReviewWorkflow` | `ProjectBugHuntWorkflow` |
| Activities | `execute_bugbot_review` / `execute_opsx_repair` / shared `execute_commit_materialize` | `execute_security_review` / `execute_security_repair` / shared `execute_commit_materialize` | `execute_project_bug_hunt` / `execute_project_bug_hunt_orch` / shared `execute_commit_materialize` |

Bugbot activity names are unchanged so existing Temporal history stays valid.

### Repair/review budget contract

`MAX_REPAIR_ROUNDS` in `config.py` counts repair activities, not reviews.
`MAX_REVIEW_RUNS` is always `MAX_REPAIR_ROUNDS + 1`, reserving a final
read-only review after the last allowed repair. The loop checks this budget
before the optional stagnation stop, so an actionable final review returns
`MAX_ROUNDS_REACHED` and never starts an eleventh repair. A clean final review
still returns the lane's clean status; uncertain and no-progress statuses are
reserved for the corresponding repair execution results.

### Post-repair commit materialization

Adversarial lanes share one transition (in-process `ReviewRepairLoop.run` and
the Temporal `_run_review_repair_loop`):

```text
repair → durable progress → /opsx-commit-all ALL GO → refresh HEAD → re-review
```

Temporal must not `git add` / `git commit`. Git writes belong exclusively to
`execute_commit_materialize`, which invokes `/opsx-commit-all ALL GO` (see
`commit_all_prompt_for_materialize()` in `config.py`). After a successful
`COMPLETE` or verified `COMPLETE_NOOP` with **product tree clean**, the workflow
reloads HEAD / candidate identity / preflight SHA. The next review must see
the new committed candidate, never the frozen original workflow HEAD.

`COMMIT_MATERIALIZE_BLOCKED` is a process failure: do not review a dirty or
unmaterialized tree. Recovery: inspect the commit-all report, then re-run the
same `/tempo-*` pipeline.

**Windows:** cursor-sdk 1.0.31 has no `local.shell` option; integrated Shell inherits
the **bridge subprocess env**. `tools/cursor_executor` sets `COMSPEC` to cmd.exe and
strips Git-Bash `MSYSTEM`/`SHELL` markers before bridge/worker start. If commit-all
still reports msys `0xC0000022`, run `/opsx-commit-all ALL GO` recovery via **Git CMD** or
PowerShell outside the SDK hop, then re-run the pipeline.

Post-hop **materialize clean** (`is_product_tree_clean` in
`commit_materialization.py`) means no dirty **or untracked** paths under product
prefixes (`src/**`, `tests/**`, `backend/**`, `openspec/changes/**`). Untracked
`opsx/**` runtime ledger / temporal run dirs do **not** block
`COMPLETE` / `COMPLETE_NOOP`. Untracked **`openspec/changes/**` does block** —
commit-all must not report parent `COMPLETE` while change-scoped OpenSpec dirs
remain (parent `PARTIAL` + `LEFT_PRODUCT_DIRTY_FOR_MATERIALIZE`).

CLEAN terminals (`BUGBOT_CLEAN` / `SECURITY_CLEAN` / `HUNT_CLEAN`) include
`candidate_sha` of the committed candidate that was actually reviewed.

## Quick start (mock)

```bash
cd tools/temporal
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash
pip install -r requirements.txt
pytest -q
python start.py --mode mock --mock-scenario scenario1
python start.py --mode mock --lane security
python start.py --mode mock --lane project-bug-hunt \
  --base stage --head save-game-progress \
  --change cross-device-novel-save-refresh
```

## Temporal mode (step-by-step UI)

`http://localhost:8233` **не откроется**, пока не запущен Temporal Server.
Сейчас у тебя Docker не был запущен — поэтому UI недоступен.

### 1. Поднять Temporal

**Вариант A — Docker (рекомендуется):**

```bash
# сначала запусти Docker Desktop
cd tools/temporal
bash scripts/start-temporal.sh
# или: docker compose up -d
```

Стек: `postgresql` + `temporalio/auto-setup` + `temporal-ui`.
UI: `http://localhost:8233`, gRPC: `localhost:7233`.

Проверка:

```bash
curl http://localhost:8233
```

**Вариант B — Temporal CLI без Docker:**

```bash
temporal server start-dev
# UI: http://localhost:8233
```

### 2. Worker + client

```bash
# terminal 1
cd tools/temporal
python worker.py --mode mock --mock-scenario scenario1

# terminal 2
python start.py --mode temporal-mock --mock-scenario scenario1
python start.py --mode temporal-mock --lane security
python start.py --mode temporal-mock --lane project-bug-hunt \
  --base stage --head save-game-progress \
  --change cross-device-novel-save-refresh
```

`start.py` выведет прямую ссылку:

```text
TEMPORAL_UI: http://localhost:8233/namespaces/default/workflows/<WORKFLOW_ID>
```

### 3. Что видно в UI

Каждый шаг — отдельная activity:

```text
ensure_cursor_session
execute_bugbot_review   # review #1
execute_opsx_repair     # repair #1
execute_commit_materialize
execute_bugbot_review   # review #2 (refreshed HEAD)
...
```

Security lane (`--lane security`):

```text
ensure_cursor_session
execute_security_review
execute_security_repair
execute_commit_materialize
execute_security_review
...
```

Project-bug-hunt lane (`--lane project-bug-hunt`):

```text
ensure_cursor_session
execute_project_bug_hunt
execute_project_bug_hunt_orch
execute_commit_materialize
execute_project_bug_hunt
...
```

Restart the **same** worker after pulling this code (do not run two workers).

Real mode:

```bash
python worker.py --mode real --repo C:/Users/vkita/my_code/game
python start.py --mode temporal-real --repo C:/Users/vkita/my_code/game
python start.py --mode temporal-real --lane security --repo C:/Users/vkita/my_code/game
python start.py --mode temporal-real --lane project-bug-hunt \
  --repo C:/Users/vkita/my_code/game \
  --base stage --head save-game-progress \
  --change cross-device-novel-save-refresh
```

## Real mode (без Temporal)

Prerequisites:

1. `agent` CLI installed and authenticated (`agent status`)
2. Optional: local Temporal server on `localhost:7233` for `temporal-real`

Probe Cursor CLI capabilities:

```bash
python start.py --mode probe --repo C:/Users/vkita/my_code/game
```

Run the full loop directly (no Temporal server required):

```bash
python start.py --mode real --repo C:/Users/vkita/my_code/game
```

Legacy Temporal block (see section above for UI setup):

```bash
# terminal 1
python worker.py --mode real --repo C:/Users/vkita/my_code/game

# terminal 2
python start.py --mode temporal-real --repo C:/Users/vkita/my_code/game
```

## Limits

- Real mode can take hours (each Cursor Agent run is a full agent episode).
- Repair activities do **not** auto-retry on ambiguous completion.
- Bugbot review allows limited technical retry only.
- This POC does not change repository code by itself; workers invoked by
  `/opsx-orch-bug-fix` may.

## Cursor CLI invocation method

```bash
agent create-chat
agent --print --output-format json --trust --force --approve-mcps \
  --workspace <repo> --resume <chat_id> "<exact prompt>"
```

Each prompt is a new CLI process bound to the same chat session.
