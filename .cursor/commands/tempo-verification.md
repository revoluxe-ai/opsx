---
description: "Temporal operator: OPSX verification execution (next-check→executor). Owner slash only."
argument-hint: "change=<id> <next-check-tier> <executor-tier>"
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


# /tempo-verification

**INTERIM (Owner GO 2026-10-29):** post-implementation happy path uses
**`/opsx-static-check FULL`** instead of tempo spine; this command remains for
legacy / frozen-plan execution only.

**Owner runs only this slash command.** No manual `python`, no separate worker
terminal, no Temporal UI steps for Owner.

You (the agent) own the full process: Temporal, worker, workflow, monitor,
process repair. Owner does not run `tempo_operator.py` — you do.

Execution boundary (all OPSX): `opsx/shared/owner-agent-execution-boundary.md`

Named kernel: `.cursor/commands/_shared/tempo-pipeline.md`

## Owner usage

```text
/tempo-verification change=<id> <next-check-tier> <executor-tier>
```

| Position | Token | Rule |
|----------|--------|------|
| first | `change=<id>` | **required** OpenSpec change id |
| last | `<next-check-tier>` | `low` \| `medium` \| `high` — `/opsx-next-check` model |
| last | `<executor-tier>` | `low` \| `medium` \| `high` — assigned bounded executor model |

No `base=` / `head=` — verification resumes from durable plan/bindings/ledger.
`VERIFICATION_SESSION_DEPENDENCY: NONE` — fresh Cursor context per hop.

If `change=` is missing → **ask once**, then stop with `STATUS: BLOCKED`.

### Model tiers when omitted (mandatory)

Parse **last two** tokens as `<next-check-tier>` and `<executor-tier>` (`low` |
`medium` | `high`). If either is missing or not a tier token:

- **Default:** use `low` for both (do not block the run).
- Owner report **must** include `REVIEW_TIER: low (default)` and
  `REPAIR_TIER: low (default)` when defaults were applied.
- Optionally ask once in chat if Owner passed only `change=<id>` and context is
  ambiguous — still proceed with `low low` in the same turn unless Owner
  explicitly supplied different tiers in the same message.

Examples:

```text
/tempo-verification change=cross-device-novel-save-refresh low low
/tempo-verification change=my-change medium low
```

## What it does (Temporal sequencing only)

```text
ensure_verification_context
→ execute_next_check (/opsx-next-check)
→ execute_assigned_check (exact CHECK_ASSIGNMENT)
→ execute_next_check (reconcile)
→ …
```

Authority preserved:

- `/opsx-next-check` = sole scheduler
- bounded executor = sole `BOUNDED_CHECK_RESULT` writer
- Temporal = sequencing only (no check selection, no repair, no plan regen)

Terminals:

| Outcome | Meaning |
|---------|---------|
| `ALL_REQUIRED_CHECKS_PASS` | frozen frontier complete |
| `VERIFICATION_CHECK_FAILED` | reconciled FAIL — stop |
| `VERIFICATION_CHECK_BLOCKED` | reconciled BLOCKED — stop |
| `BLOCKED_SNAPSHOT_DRIFT` / `CONTRACT_ERROR` | stop |
| `EXECUTOR_COMPLETION_UNCERTAIN` | stop; inspect expected_result_path |

Do **not** auto-run `/opsx-verify` archive after success.

## Post-completion: owner-done-signal (mandatory when terminal)

When the pipeline finishes with any terminal outcome (`PASS`, `FAIL`,
`BLOCKED`, or uncertain `STOP`), run the PP step **`owner-done-signal`**:

1. `tempo_operator.py run` emits `post_completion` + `done_page_url` in JSON.
2. Open that URL in Owner's **existing** Chrome via **user-chrome-devtools**
   `navigate_page` (no new profile, no MCP restart).
3. Owner should see: **«Готово, всё, я знаю, что готово, перехватываю
   управление»**.

Asset: `tools/temporal/assets/owner-done-signal.html`
Spec: `pipelines.json` → `verification.post_completion_steps`

**Distinct from `owner-browser-check-release`** (post-smoke only, not pipeline
terminal) — see **Browser check UX** below.

## Browser check UX (smoke_acceptance / opsx-browser-check)

### Cloud Agent — Tempo ↔ parent-agent handoff (default)

On Cloud VM, nested Temporal `agent` **cannot** use `cursor-ide-browser`. **Do
not** wait for Owner GO. **Do not** add CDP/puppeteer in Tempo.

When Tempo reaches `smoke_acceptance` CURRENT (or nested smoke **BLOCKED**):

1. `kill-workers` if workflow stuck on `execute_assigned_check`.
2. Write handoff manifest:
   `opsx/runs/temporal/gates/cloud-browser-handoff/<change-id>.json`
3. **Parent chat agent** (this session) runs **`/opsx-browser-check`** using the
   same browser path as normal Cloud Agent testing (`computerUse` when
   available).
4. Write bounded `verify/check-results/.../smoke_acceptance.json` +
   `verify/BROWSER-CHECK.md`.
5. Reconcile:
   `node opsx_tools/opsx-verification-ledger.mjs reconcile --change <id>`.
6. Re-run `/tempo-verification change=<id> <tiers>` for `security_review` +
   `frontend_build`.

Full protocol: `tools/temporal/CLOUD-BROWSER-HANDOFF.md` · `AGENTS.md`.

### Owner-local Windows (manual `/opsx-browser-check` only — not Cloud Tempo)

When Owner runs browser smoke **outside** Tempo on a Windows machine with Owner
Chrome + `user-chrome-devtools`:

1. Post `chat_instruction.markdown` + open focus gate page.
2. Owner writes **GO**; record ack via `owner-browser-check-go`.
3. Run `/opsx-browser-check` in Owner Cursor.

Retired for Cloud `/tempo-verification`: mandatory GO before Tempo smoke.

### Post — browser release page (optional, Owner-local)

When smoke executor finishes (`browser_check_release` in run JSON):

1. Open `browser_check_release_page_url` via **user-chrome-devtools**
   `navigate_page`.
2. Post in chat `browser_check_release_chat_message`, e.g.
   **«Браузерная проверка завершена (PASS). Можно пользоваться браузером.»**

Asset: `tools/temporal/assets/owner-browser-check-release.html`
Spec: `pipelines.json` → `verification.post_browser_check_steps`

Do **not** confuse with `owner-done-signal` (full pipeline terminal only).

## Pre-assignment: owner-chrome-focus-gate (RETIRED on Cloud Tempo)

`evaluate_pre_run_gate` returns `None`. Cloud Tempo uses parent-agent handoff
(see above). Owner GO ack CLIs remain for Owner-local manual browser only.

## Agent execute (single entry — mandatory)

Parse `$ARGUMENTS` (models = **last two tokens**, with tier defaults above),
then run (**global `--repo` before** the `run` subcommand):

```bash
python tools/temporal/runtime.py --repo <workspace> run verification \
  --change <id> \
  --review <next-check-tier> \
  --repair <executor-tier>
```

(`--review` / `--repair` map to `next_check` / `verification_executor` steps.)

**Wrong (will fail argparse):** `runtime.py run verification --repo …` — never
document or invoke that order.

This alone: Temporal → worker → workflow → wait → report.

### `PIPELINE_ALREADY_RUNNING` recovery (mandatory — agent-owned)

When `run verification` returns `BLOCKED` / `reason: PIPELINE_ALREADY_RUNNING`,
or `status` shows the same change workflow **RUNNING** while **no** `worker.py`
is alive (zombie operator/workflow):

1. `python tools/temporal/runtime.py --repo <workspace> kill-workers`
2. Cancel the stuck workflow (same id as in run JSON / status), e.g. Temporal
   client `cancel()` on
   `verification-execution-poc-real-<mode>-<change-id>` — agent runs this; Owner
   does not.
3. Confirm workflow is not `RUNNING`; then **one** re-invocation of the same
   `run verification` line above with the same tiers.
4. If still blocked after one recovery cycle → stop with `STATUS: BLOCKED`,
   `OUTCOME: PIPELINE_ALREADY_RUNNING`, and `NEXT_ACTION` describing workflow id
   + ledger frontier (do not spin forever).

Do **not** tell Owner to run python/temporal commands manually.
Do **not** hunt/patch product code or regenerate the verification plan.

## Owner report (when done)

```text
STATUS: PASS | FAIL | BLOCKED
PIPELINE: verification
CHANGE: ...
PLAN_ID: ...
PROGRESS: n/N
REVIEW_TIER: ...   (= next_check)
REPAIR_TIER: ...   (= verification_executor)
WORKFLOW_ID: ...
TEMPORAL_UI: ...
OUTCOME: ...
NEXT_ACTION: ...
DONE_PAGE: opened | skipped | failed
BROWSER_CHECK_INSTRUCTION: posted yes/no
BROWSER_CHECK_RELEASE_PAGE: opened yes/no
```

## Do not

- Do not pass `base=` / `head=` from Owner args.
- Do not tell Owner to run python/temporal commands.
- Do not select checks, invent executors, or repair after FAIL.
- Do not claim ARCHIVE_GO from this pipeline alone.
