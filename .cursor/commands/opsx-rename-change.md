---
component_id: opsx.commands.rename_change
component_version: 0.2.0
ownership: TARGET_ONLY
runtime_class: ACTIVE
source_commit: 91311ada29721cfb9aab7a00e1dc4d2a0a8d1982
source_path: .cursor/commands/opsx-rename-change.md
required_adapters: ["opsx-lite", "project-generic"]
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
contract_id: OPSX-WB-RENAME-CHANGE-01
---


# TARGET OVERRIDE — HIGHEST PRIORITY

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


# /opsx-rename-change

Analyze an active change's current planning content, propose scope-true kebab
names, and apply one explicitly selected directory rename with `git mv`.
Content revision remains owned by `/opsx-update`; evolution-card retargeting
remains owned by `/opsx-evolution`.

## User input

```text
$ARGUMENTS
```

Supported forms:

```text
/opsx-rename-change change=<old-kebab>
/opsx-rename-change change=<old-kebab> <N> GO
/opsx-rename-change change=<old-kebab> GO <N>
/opsx-rename-change change=<old-kebab> to=<custom-kebab> GO
```

Acknowledgement, `ok`, or an option number without exact `GO` never writes.

## Target path authority

Resolve OpenSpec status first and use its `planningHome.changesDir` and
`changeRoot`; do not assume another store. The archive sibling is
`<planningHome.changesDir>/archive/YYYY-MM-DD-<change>`. Refuse archive paths.

Read-only linked-change evidence may come from `openspec/evolution/**`.
Do not rewrite evolution cards in this command.

## Modes

| Input | Mode | Writes |
|---|---|---|
| no exact `GO` | `ANALYZE` | none |
| exact `GO` plus one fresh option number or `to=<kebab>` | `APPLY` | one `git mv` of the active `changeRoot` |

## ANALYZE

1. Resolve `change=`; infer only when exactly one active change is relevant.
2. Confirm the active `changeRoot` exists and is outside the archive.
3. Bounded-read `proposal.md`, relevant `design.md`/`tasks.md` sections, and
   at most one delta spec when needed.
4. Summarize `CONTENT_ESSENCE` without inventing scope.
5. Emit 3–5 distinct kebab options, each with `WHY`; recommend one.
6. Search `openspec/evolution/**` for the old slug and report `LINKED`.
7. Stop without running `git mv`.

## APPLY

1. Resolve exactly one choice from the latest same-chat options or
   `to=<kebab>`. A stale/missing option list requires a new ANALYZE.
2. Validate `[a-z0-9]+(-[a-z0-9]+)*`.
3. Re-resolve status immediately before the write.
4. Require old `changeRoot` to exist, target path to be absent, both paths to
   share the resolved `planningHome.changesDir`, and neither path to be under
   `archive`.
5. Run exactly one PowerShell-safe command:

   ```powershell
   git mv -- "<resolved-old-change-root>" "<resolved-new-change-root>"
   ```

6. Do not edit any artifact, stage other paths, commit, push, checkout, reset,
   clean, stash, archive, or delete.

## Failure and recovery

| Condition | Status |
|---|---|
| Missing/ambiguous active change | `BLOCKED_NEEDS_CHANGE` |
| `GO` without a choice | `BLOCKED_NEEDS_CHOICE` |
| Number without a fresh options list | `BLOCKED_NEEDS_ANALYZE` |
| Number outside the list | `BLOCKED_BAD_CHOICE` |
| Invalid kebab | `BLOCKED_BAD_SLUG` |
| Target already exists | `BLOCKED_TARGET_EXISTS` |
| Path escapes resolved changes directory or enters archive | `BLOCKED_PATH_AUTHORITY` |
| `git mv` fails | `BLOCKED_GIT_MV` |

## Output

ANALYZE:

```text
STATUS: OPTIONS_READY
MODE: ANALYZE
OLD_CHANGE:
CONTENT_ESSENCE:
OPTIONS:
1. <kebab>
   WHY:
RECOMMENDED:
LINKED: yes | no
OWNER_DECISION_REQUIRED:
NEXT_ACTION: /opsx-rename-change change=<old> <N> GO
```

APPLY:

```text
STATUS: RENAMED | RENAMED_NEEDS_EVOLUTION_RETARGET | BLOCKED_*
MODE: APPLY
OLD_CHANGE:
NEW_CHANGE:
CHOICE:
LINKED: yes | no
GIT_MV: ok | failed | not_run
WHAT_HAPPENED:
WHY:
OWNER_DECISION_REQUIRED:
NEXT_ACTION:
```

After a linked rename, route to `/opsx-evolution GO` for retargeting. Route
remaining planning-content drift to `/opsx-update <new>`.
