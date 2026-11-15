# Cloud Browser Handoff — Tempo ↔ Parent Agent

**Policy:** On Cloud Agent VMs, nested `agent` CLI hops **cannot** use
`cursor-ide-browser` MCP. Do **not** add CDP/puppeteer/Owner-Chrome fallbacks in
Tempo. Use this **two-phase** workflow instead.

## When to hand off

Hand off when **all** are true:

1. Pipeline: `/tempo-verification change=<id> …`
2. Ledger `current_assignment.capability_id` = `smoke_acceptance` (executor
   `opsx-browser-check`)
3. Nested executor wrote `BLOCKED` / `BROWSER_RUNTIME_UNAVAILABLE`, **or**
   workflow is stuck on `execute_assigned_check` for smoke, **or**
   `GetDynamicTools` has no `cursor-ide-browser` namespace

Do **not** hand off for `frontend_tests`, `security_review`, or `frontend_build`
— Tempo nested agent continues those normally.

## Phase 1 — Tempo (automated)

```bash
python tools/temporal/tempo_operator.py --repo <repo> run verification \
  --change <change-id> --review low --repair low
```

Tempo runs until:

- `frontend_tests` reconciled **PASS** (typical), and
- `execute_assigned_check` targets `smoke_acceptance`, then stops/fails/blocks

**Stop Tempo** before parent browser work if workflow is stuck:

```bash
python tools/temporal/tempo_operator.py --repo <repo> kill-workers
# cancel stuck tempo_operator run if still waiting (kill its PID)
```

Record handoff:

```bash
# Agent writes (create dir if needed):
# opsx/runs/temporal/gates/cloud-browser-handoff/<change-id>.json
```

Manifest fields: `change_id`, `plan_id`, `required_check_id`, `assignment_epoch`,
`handoff_at`, `reason` (e.g. `NESTED_AGENT_NO_BROWSER_MCP`), `resume_command`.

## Phase 2 — Parent agent (browser)

The **chat Cloud Agent** (Composer), not nested `agent` CLI, runs browser smoke:

1. Ensure serve: `npm run serve` → `http://127.0.0.1:8080/` HTTP 200
2. Execute **`/opsx-browser-check`** contract for the frozen assignment:
   - Read `verify/EXECUTOR-BINDINGS.json` scenario_steps
   - Use parent-agent browser (`computerUse` / embedded browser when available)
   - **No Owner GO** on Cloud Tempo path
3. Write durable evidence **only** to assignment paths:
   - `verify/check-results/<plan_id>/<check>--smoke_acceptance.json` → `STATUS: PASS|FAIL|BLOCKED`
   - `verify/BROWSER-CHECK.md` → `BROWSER_CHECK_PASS|FAIL|BLOCKED`
4. Reconcile ledger (does **not** schedule next check):

```bash
node opsx/tools/opsx-verification-ledger.mjs reconcile --change <change-id>
```

If smoke **PASS**, ledger advances smoke → **PASS** and schedules next PENDING
(typically `security_review`).

If smoke **BLOCKED/FAIL**, stop — do not resume Tempo until fixed.

## Phase 3 — Resume Tempo (automated)

After smoke **PASS** + reconcile:

```bash
python tools/temporal/tempo_operator.py --repo <repo> run verification \
  --change <change-id> --review low --repair low
```

Tempo continues frozen order: `security_review` → `frontend_build` →
`ALL_REQUIRED_CHECKS_PASS`.

Do **not** re-run smoke unless ledger regressed to CURRENT+BLOCKED.

## Owner involvement

| Step | Owner action |
|------|----------------|
| Tempo through unit tests | None |
| Cloud browser handoff | None (parent agent) |
| Owner-local Windows only | Optional `user-chrome-devtools` if doing manual `/opsx-browser-check` outside Tempo |

Retired for Tempo: Owner GO, `owner-chrome-focus-gate`, physical Chrome click
(Windows local path only).

## Quick checklist (agent)

```text
[ ] Tempo reached smoke_acceptance CURRENT
[ ] kill-workers if nested hop stuck
[ ] Write cloud-browser-handoff manifest
[ ] Parent /opsx-browser-check → PASS + bounded JSON + BROWSER-CHECK.md
[ ] opsx-verification-ledger.mjs reconcile --change <id>
[ ] Re-run tempo-verification (low low)
[ ] Terminal ALL_REQUIRED_CHECKS_PASS or stop on FAIL/BLOCKED
```

## References

- `.cursor/commands/tempo-verification.md` — slash command + handoff section
- `.cursor/commands/_shared/tempo-pipeline.md` — kernel + Cloud handoff
- `AGENTS.md` — Cloud Agent default verification flow
- `.cursor/commands/opsx-browser-check.md` — browser evidence contract
