# /tempo-* shared operator kernel

Shared contract for Temporal pipeline operator commands (`/tempo-*`).
One command = one pipeline.

**Owner interface:** slash command only.  
**Agent interface:** `python tools/temporal/runtime.py run вЂ¦` (one entry).

This is **not** `/opsx-pipeline` and not an OPSX product hop.

## Owner vs agent

| Who | Does what |
|-----|-----------|
| **Owner** | `/tempo-<pipeline> <review> <repair> вЂ¦` вЂ” nothing else |
| **Agent** | Parse args в†’ `tempo_operator.py run` в†’ monitor в†’ report |

Never hand Owner a checklist of `worker.py`, `start.py`, or `resolve-args`.
If Temporal/Docker is down and auto-repair fails, `STATUS: BLOCKED` with one
line: start Docker and rerun the **same slash command**.

## What `run` does (agent-internal)

1. Parse review/repair tiers + pipeline-specific keys from `$ARGUMENTS`
2. Auto-resolve values (example: `head` = `git branch --show-current`)
3. Ensure Temporal Server
4. Ensure exactly one worker (reuse if same tiers; else restart)
5. Start workflow on the queue and wait for pipeline completion
6. Repair **process** only (server, worker, duplicates, console encoding)

Machine spec: `tools/temporal/pipelines.json`  
Implementation: `tools/temporal/tempo_operator.py`

## Required arguments (all /tempo-*)

Pipeline-specific keys first (`change=`, `base=`, вЂ¦ per command body).
When `change=` is omitted, resolve default change per
`opsx-source-resolution.md` § CHANGE CONTEXT (`.tmp/change-config.toml` from
`/opsx-new`, `/opsx-ff`, or `/opsx-current-change set`).
**Model tiers last** вЂ” always the final two tokens:

```text
/tempo-bugbot [base=main] <review-tier> <repair-tier>
/tempo-security [base=main] <review-tier> <repair-tier>
/tempo-project-bug-hunt change=<id> [base=main] <review-tier> <repair-tier>
/tempo-verification change=<id> <next-check-tier> <executor-tier>
```

For `/tempo-bugbot` and `/tempo-security`, only optional `base=` appears before the two tiers.
For `/tempo-project-bug-hunt`, `change=` is required before tiers.
For `/tempo-verification`, `change=` is required; no `base=`/`head=`.

Tiers: `low` | `medium` | `high` вЂ” both required, no default.

## Agent steps

### 1. Parse `$ARGUMENTS`

Per command body. Missing required key в†’ `STATUS: BLOCKED`, ask once.

### 2. Run pipeline (single command)

```bash
python tools/temporal/runtime.py run <pipeline-id> \
  --repo <workspace> \
  --review <review> \
  --repair <repair> \
  ... pipeline flags ...
```

Run in **background** when the hunt will take long; poll `status` if needed.

### 3. Monitor (optional mid-flight)

```bash
python tools/temporal/runtime.py --repo <repo> status --workflow-id <id>
```

Healthy: `RUNNING`, trail grows session в†’ review в†’ repair в†’ review, no
`failed_activities`, worker alive.

### 4. Process repair (agent only)

| Symptom | Action |
|---------|--------|
| Temporal down | `run` retries `docker compose`; else BLOCKED |
| Worker dead | rerun `run` (restarts worker) |
| Duplicate workers | `kill-workers` then `run` |
| Identical PP already RUNNING | `STATUS: BLOCKED` `PIPELINE_ALREADY_RUNNING` вЂ” wait; do not start a second |
| `UnicodeEncodeError` after COMPLETED | not pipeline fail; read `result_file` |
| Activity FAILED | restart via `run` once; then FAIL |

### 5. Report to Owner

```text
STATUS: PASS | FAIL | BLOCKED
PIPELINE: <id>
REVIEW_TIER: ...
REPAIR_TIER: ...
BASE: ...
HEAD: ...
CHANGE: ...        (when applicable)
WORKFLOW_ID: ...
OUTCOME: ...
REVIEW_RUNS: ...
REPAIR_ROUNDS: ...
WORKER: reused | started
PROCESS_REPAIRS: none | ...
DONE_PAGE: opened | skipped | failed
```

Do **not** paste `TEMPORAL_UI` / `localhost:8233` links to Owner unless they
explicitly ask. Owner contract is slash command + terminal `STATUS` only
(see command bodies: «no Temporal UI steps for Owner»). Agents may use
`runtime.py status --workflow-id …` internally; UI on VM is optional diagnostics
(Ports **8234** or Desktop browser on the agent VM).

### 6. Post-completion PP steps (when terminal)

When the workflow reaches a terminal outcome (`PASS` / `FAIL` / `BLOCKED` /
`STOP`), run configured `post_completion_steps` from `pipelines.json`.

**`owner-done-signal`** (verification pipeline):

1. Operator writes manifest under `.tmp/temporal/owner-done-signal/`.
2. Agent opens the static page in Owner Chrome via **user-chrome-devtools**
   `navigate_page` to the `done_page_url` from the run JSON (or
   `tools/temporal/assets/owner-done-signal.html` as `file://` URL).
3. Page text (Russian): В«Р“РѕС‚РѕРІРѕ, РІСЃС‘, СЏ Р·РЅР°СЋ, С‡С‚Рѕ РіРѕС‚РѕРІРѕ, РїРµСЂРµС…РІР°С‚С‹РІР°СЋ
   СѓРїСЂР°РІР»РµРЅРёРµВ».

Do **not** spawn a new Chrome profile or restart MCP. Reuse the existing
`--autoConnect` session (see `.cursor/rules/chrome-devtools-mcp-session.mdc`).

### 7. Browser check UX (verification / smoke_acceptance)

#### Cloud Agent вЂ” Tempo в†” parent-agent handoff (default on Cloud VM)

Nested Temporal `agent` hops **lack** `cursor-ide-browser` MCP. **Do not** add
CDP/puppeteer fallbacks in Tempo. Protocol:

1. Tempo runs until ledger `smoke_acceptance` is CURRENT (see
   `tools/temporal/CLOUD-BROWSER-HANDOFF.md`).
2. Stop stuck worker: `kill-workers`.
3. **Parent chat agent** runs `/opsx-browser-check` (same browser tools as normal
   Cloud Agent work вЂ” `computerUse` / embedded browser when available).
4. Write bounded `check-results/**` + `verify/BROWSER-CHECK.md`; reconcile:
   `node opsx_tools/opsx-verification-ledger.mjs reconcile --change <id>`.
5. Re-run `/tempo-verification` for remaining checks (`security_review`,
   `frontend_build`).

Record handoff manifest:
`opsx/runs/temporal/gates/cloud-browser-handoff/<change-id>.json`

#### Owner-local Windows (optional manual path)

**Pre (chat + focus gate):** when running `/opsx-browser-check` **outside** Tempo
on Owner machine, post `chat_instruction.markdown` and open `gate_page_url` via
`user-chrome-devtools` `navigate_page`. **Not used** on Cloud Tempo handoff.

**Post (release only вЂ” not pipeline terminal):** when run JSON includes
`browser_check_release_page_url` (or executor finished smoke), open that URL via
`navigate_page`. Page: В«Р‘СЂР°СѓР·РµСЂРЅР°СЏ РїСЂРѕРІРµСЂРєР° Р·Р°РІРµСЂС€РµРЅР° вЂ” РјРѕР¶РЅРѕ СЃРЅРѕРІР°
РїРѕР»СЊР·РѕРІР°С‚СЊСЃСЏ ChromeВ». Asset:
`tools/temporal/assets/owner-browser-check-release.html`. Distinct from
`owner-done-signal`.

Manual instruction manifest:

```bash
python tools/temporal/runtime.py owner-browser-check-instruction verification \
  --change <id>
```

Manual release manifest:

```bash
python tools/temporal/runtime.py owner-browser-check-release verification \
  --change <id>
```

## Write boundary

Allowed: `tools/temporal/**`, `.tmp/temporal/**`, `/tempo-*` commands, this file.

Forbidden: product writes outside resolved TARGET product roots, git commit/push, editing OPSX command bodies.

## New /tempo-* sibling

1. Entry in `pipelines.json`
2. `run` support in `tempo_operator.py` (or shared runner)
3. `.cursor/commands/tempo-<name>.md` вЂ” Owner args + `PIPELINE_ID` only

| Command | Pipeline id | Required keys |
|---------|-------------|---------------|
| `/tempo-bugbot` | `bugbot` | none (`base=` optional) |
| `/tempo-security` | `security` | none (`base=` optional) |
| `/tempo-project-bug-hunt` | `project-bug-hunt` | `change=` |
| `/tempo-verification` | `verification` | `change=` |
