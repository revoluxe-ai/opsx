---
name: /opsx-break-glass
id: opsx-break-glass
category: Workflow
description: Owner one-shot OPSX process-gate override (no secrets); agent runs CLI and records debt
argument-hint: "[list-open | activate | issue-universal-v2-grant | revalidate]"
---

# /opsx-break-glass

**Owner contract:** Owner sends only `/opsx-break-glass` (optional args for diagnostics: `list-open`). Owner does **not** run terminal commands, does **not** `export BG_*`, and does **not** configure HMAC or grants.

**Agent contract:** On this command, the **agent** runs the existing break-glass CLI from the **repository root** via Shell, verifies journals, then continues feature work only through the supported exception route. Report the **actual** CLI and journal outcome — never fabricate PASS, `VERIFY_PASS`, `GREEN_PROVEN`, or merge/deploy/archive authority.

## What break glass does

Bypass for **OPSX process gates** on the current change. Skipped gates are recorded as **`OWNER_OVERRIDDEN`** in debt and incidents (`CONTINUE_WITH_EXCEPTION`). Does not auto-approve merge, deploy, or archive. Unsupported gates remain honest blockers.

## Agent execution (mandatory)

Execute in order:

1. **Resolve context** — current feature checkout and OPSX change:
   - Active `super-implement/RUN.json` with status `RUNNING` or `PAUSED`, else a single writer lock, else change root / id from this chat (see scenarios below).
   - `design.change_id` comes from `apply-design/APPLY-DESIGN.json` (not git branch name alone).
2. **Run CLI** (repo root, Shell tool — **not** Owner terminal):

```bash
node opsx_tools/opsx-break-glass-cli.mjs
```

Writes append-only **debt** (`opsx/break-glass/debt.jsonl`) and **incidents** (`opsx/break-glass/incidents.jsonl`). Materializes change-scoped synthetic process evidence at `openspec/changes/<change>/apply-process-exception/PROCESS-GATE-DEFERRAL.json` (`provenance: OWNER_OVERRIDE`; preserves original FAIL; no fake GREEN/PASS). Subsequent OPSX process gates (split-APPLY adapter, super-implement recovery/selector, bounded-verify compose) honor the open `CONTINUE_WITH_EXCEPTION` row for the same change. Existing incidents can backfill deferral without re-activation: `node opsx_tools/opsx-break-glass-process-gate-deferral.mjs ensure-deferral --change-id <id>`.

When auto-resolution is insufficient but the change is **unambiguous from chat context**, pass supported flags (prefer `--change-root`):

```bash
node opsx_tools/opsx-break-glass-cli.mjs --change-root openspec/changes/<folder>
```

Optional agent-only CLI flags (use instead of asking Owner for env vars):

- `--change-id <design.change_id>`
- `--change-root <repo-relative path>`
- `--execution-id <id>` (default in CLI: `exec:<design.change_id>`)
- `--blocked-gate <gate>` (default: `ANY`)
- `--failure-code <code>` (default: `UNKNOWN`)
- `--revision <sha>` (when set, must match git or CLI fails closed)

The agent may set `BG_*` / `OPSX_CHANGE_*` **only inside the Shell invocation environment** when needed; **never** document or request Owner `export` of those variables.

3. **Interpret CLI JSON** — exit `0` and `ok: true` with `gate_check_status: OWNER_OVERRIDDEN` on success; exit `2` on resolution / identity failure — stop and report the JSON error (no fake override).
4. **Verify durability** — read append-only journals under `opsx/break-glass/`:
   - `debt.jsonl` — latest row for this `change_id` shows `OWNER_OVERRIDDEN` / open exception debt as applicable.
   - `incidents.jsonl` — matching open `CONTINUE_WITH_EXCEPTION` for the same change/execution when CLI succeeded.
5. **Continue development** — if and only if step 3–4 confirm override: resume the blocked OPSX/feature step via the supported exception route (split-APPLY adapter `OWNER_OVERRIDE`, super-implement recovery/selector). If the gate is **not** in the supported process-gate set, emit a **blocker** with the real failure code; do not claim the gate passed.
6. **Report** — state `change_id`, CLI exit, `gate_check_status`, journal evidence (paths + last event ids/keys), and next OPSX action. Forbidden: instructing Owner to run `node …`, copy-paste terminal recipes, or treat Owner chat text as merge/deploy/archive GO.

## Chat-context scenarios (no Owner terminal)

| Change (design id / folder) | When auto-resolve fails |
|----------------------------|-------------------------|
| `invest-yalta-max-funnel` → `openspec/changes/invest-yalta-max-funnel` | Agent passes `--change-root openspec/changes/invest-yalta-max-funnel` |
| `external-client-notifications-pilot` → `openspec/changes/external-client-notifications-pilot` | Agent passes `--change-root openspec/changes/external-client-notifications-pilot` |

## Owner-forbidden / agent-forbidden

- Owner: manual CLI, `export BG_*`, HMAC setup, grant issuance, extra GO for override.
- Agent: delegating CLI to Owner; fake evidence; claiming unsupported gates are cleared; using override as merge/deploy/archive permission.

## Diagnostics

Owner may pass `list-open`; agent runs:

```bash
node opsx_tools/opsx-break-glass-cli.mjs list-open
```

## Legacy HMAC paths (deprecated)

Subcommands `activate`, `issue-universal-v2-grant`, and `revalidate` exist for hermetic regression tests only. Production Owner workflow is default CLI invocation by the **agent** as above.
