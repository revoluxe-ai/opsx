---
name: /opsx-current-change
id: opsx-current-change
category: Workflow
description: "Bind local default OpenSpec change in .tmp/change-config.toml (gitignored); downstream opsx-* may omit change= when resolver is wired"
argument-hint: "[show | set <change-id> | clear | validate] [change-id]"
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


# /opsx-current-change

**Role:** Persist the **current OpenSpec change id** for this repo worktree in a
**local, non-git** file so Owner and agents can run `change=`-optional OPSX hops
after companion resolver wiring (see `REQUIRED_EXTERNAL_FIXES`).

**Does not:** replace explicit `change=` in arguments; auto-pick when multiple
active changes exist without binding; write to git; override break-glass execution
identity; imply verify/archive GO.

## Local artifact (SSOT for default change)

```text
.tmp/change-config.toml
```

Must be listed in `.gitignore` (`.tmp/`). Other files under `.tmp/` (run logs,
Temporal receipts, etc.) may coexist; only `change-config.toml` is the change
binding. Supervisor reports remain under `.tpm/` (`opsx-supervisor`).

### TOML schema `opsx.current_change.v1`

```toml
schema = "opsx.current_change.v1"
change_id = "<CHANGE_ID>"
change_root = "openspec/changes/<CHANGE_ID>"
revision_at_set = "<git HEAD sha>"
set_at_utc = "<ISO-8601 UTC>"
set_by = "/opsx-current-change"
```

## Change context resolution (contract for all consumers)

When a command accepts optional `change=` / `change-id`, resolve in this order:

```text
1. Explicit change token in $ARGUMENTS (always wins)
2. Valid binding in `.tmp/change-config.toml` (change_id + existing change_root/)
3. Existing command-specific fallbacks (chat context, single active change, ASK, BLOCKED)
```

Until `opsx-source-resolution.md` and helpers are updated, step 2 is **honored
only by commands that document this file** — binding still helps humans and
future resolver rollout.

## User input

```text
$ARGUMENTS
```

| Token | Action |
|-------|--------|
| (empty) or `show` | Read and display current binding; validate change root |
| `set <change-id>` | Write `.tmp/change-config.toml` after validation |
| `<change-id>` alone | Same as `set <change-id>` if token matches change id pattern |
| `clear` | Remove binding (delete file or empty schema) |
| `validate` | Read-only: binding valid? change folder exists? |

Change id pattern: `[a-z0-9][a-z0-9-]*` (same spirit as OpenSpec folder names).

## Projectization gate

Read `opsx/projectization.yaml`. If `status` != `COMPLETE` or
`normal_commands_enabled` is false:

```text
STATUS: FAIL_CLOSED
REASON: OPSX_PROJECTIZATION_REQUIRED
NEXT_ACTION: /opsx-projectize
```

## Agent execution (mandatory)

1. **Preflight** — repo root; `git rev-parse HEAD` (read-only) for `revision_at_set`.
2. **Parse** subcommand from `$ARGUMENTS`.
3. **Validate change id** (set/validate):
   - Pattern check.
   - `Test-Path` / filesystem: `openspec/changes/<change-id>/` exists (at least
     `proposal.md` or `design.md` or `owner-contract.md` under that tree).
4. **set** — prefer `tools/openspec/opsx_change_context.py`:
   - `write_current_change_binding(repo_root, change_id, revision_at_set=<HEAD>)`
   - or equivalent TOML write matching schema below;
   - helper also **deletes** misplaced `.tpm/change-config.toml` if present (`.tpm/`
     is supervisor-only, not change binding);
   - on `ValueError` → `FAIL_CLOSED` / invalid id or missing root.
5. **clear** — `clear_current_change_binding(repo_root)`.
6. **show / validate (read-only)** — `load_current_change(repo_root)`; if file missing:

```text
STATUS: UNBOUND
CURRENT_CHANGE: (none)
NEXT_ACTION: /opsx-current-change set <change-id>
```

7. **set** (continued) — report:

```text
STATUS: BOUND
CURRENT_CHANGE: <change-id>
CHANGE_ROOT: openspec/changes/<change-id>
REVISION_AT_SET: <sha>
ARTIFACT: .tmp/change-config.toml
NEXT_ACTION: /opsx-verify mode=archive   # example without change=
```

8. **clear** — report:

```text
STATUS: CLEARED
NEXT_ACTION: /opsx-current-change set <change-id>
```

## Write boundary

**May write:** `.tmp/change-config.toml` and create `.tmp/` directory only.

**Must not write:** `openspec/**`, `src/**`, `opsx/runs/**`, git index, committed
`.cursor/commands/**` except this command’s own file at create time.

**Must not:** `git commit`, `git push`, break-glass, adoption, or fake verify PASS.

## Read boundary

- `.tmp/change-config.toml`
- `openspec/changes/<change-id>/` (existence check only)
- `opsx/projectization.yaml`
- `git rev-parse HEAD` (read-only)

## Output contract

```text
STATUS: BOUND | CLEARED | UNBOUND | SHOW | VALID | FAIL_CLOSED
MODE: READ_ONLY | WRITE_LOCAL_TMP
COMMAND: /opsx-current-change
CURRENT_CHANGE:
CHANGE_ROOT:
REVISION_AT_SET:
ARTIFACT: .tmp/change-config.toml
VALIDATION: OK | MISSING_CHANGE_ROOT | INVALID_CHANGE_ID | UNBOUND

WHAT_HAPPENED:
WHY:
NEXT_ACTION:
DO_NOT_DO_YET:
```

## When to use

- Start of a feature session: one `set`, then shorter slash commands (after resolver companion).
- Switching changes: `clear` then `set` other id.
- Debugging “which change is active?” → `show`.

## When not to use

- CI / shared runners (no stable local `.tmp` binding).
- Parallel work on **two** changes in one worktree without explicit `change=` per hop.
- Replacing `design.change_id` in break-glass / materialize identity (those keep explicit ids).

## REQUIRED_EXTERNAL_FIXES (companion rollout — track in fix-command-defects)

Done in companion apply (this integration batch):

- `opsx-source-resolution.md` — CHANGE CONTEXT RESOLUTION
- `tools/openspec/opsx_change_context.py`
- `.gitignore` — `.tmp/` (change binding); `.tpm/` remains for supervisor-only artifacts
- `/opsx-new`, `/opsx-ff`, skills mirrors — auto-bind after scaffold
- `/opsx-verify` — optional `change=` via resolver

Remaining (optional):

- Other `change=`-required commands (orch, scenario-coverage) — same resolver note
- `tempo_operator.py` calling `resolve_change_id` at CLI boundary

## Hard rules

- Explicit `change=` in the same invocation **always** overrides `.tmp` binding.
- Never bind a change id without an existing `openspec/changes/<id>/` directory.
- Never commit `.tmp/change-config.toml` or other `.tmp/` contents.
- Do not claim downstream commands omit `change=` until companion (1)–(2) land.

## Relationship to other commands

| Command | Relationship |
|---------|----------------|
| `/opsx-meta-consult` | May read `.tmp` binding for orientation |
| `/opsx-break-glass` | Still prefers `--change-root` / design id; `.tmp` is not execution identity |
| `/opsx-pipeline` | Navigation; `.tmp` binding reduces repeated `change=` typing |
| `/opsx-meta-fix-command-defects` | Owns resolver + gitignore companions |

## Post-create Owner workflow

```text
/opsx-current-change set invest-yalta-max-funnel
/opsx-current-change show
# after companion fixes:
/opsx-verify mode=archive
```

Process commit for this new command file: `/opsx-meta-commit` (not `/opsx-commit`).
