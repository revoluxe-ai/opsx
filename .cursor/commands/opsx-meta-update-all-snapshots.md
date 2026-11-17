---
component_id: opsx.commands.meta_update_all_snapshots
component_version: 0.1.0
ownership: TARGET_ONLY
runtime_class: ACTIVE_WITH_LIMITS
source_commit: 91311ada29721cfb9aab7a00e1dc4d2a0a8d1982
source_path: .cursor/commands/opsx-meta-update-all-snapshots.md
required_adapters: ["opsx-lite", "project-generic"]
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
contract_id: OPSX-WB-META-SNAPSHOT-ALL-01
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


# /opsx-meta-update-all-snapshots

Fan-out **planning** owner for publishing committed allowlisted process trees
from every live `SOURCE.yaml` worktree into one shared central
(`sources/<family>/<branch>/`). `/opsx-meta-update-snapshot` remains the
**single-repo** owner for the invoking checkout only.

On this TARGET, the catalog helper
`node opsx_tools/opsx-central-catalog.mjs` accepts `--repo` and `--head-only`
but **does not** implement `--central`. Foreign-worktree `snapshot-apply` without
a shared central override is how a second on-disk central was created on SOURCE;
this adaptation therefore keeps inventory/planning useful while **blocking APPLY**.

```text
IMPLEMENTATION_STATUS: IMPLEMENTED_WITH_LIMITS
RUNTIME_STATUS: ACTIVE_WITH_LIMITS
CONTRACT_ID: OPSX-WB-META-SNAPSHOT-ALL-01
MODE: READ_ONLY (default) | APPLY blocked on TARGET until tool gains --central
SOURCE_PARITY: PARTIAL
GO_SUPPORTED: false
READ_ONLY: true (inventory and per-row snapshot-plan classification)
WRITES: none on TARGET today (no snapshot-apply, no catalog/index)
```

Tool (read/plan only): `node opsx_tools/opsx-central-catalog.mjs snapshot-plan`
Inventory SoT: `<CENTRAL_ROOT>/sources/**/SOURCE.yaml` (`source_repo`, `source_branch`)
Config bind (invoking repo): `opsx/sources.yaml`

Protected siblings (authority unchanged): `/opsx-meta-update-snapshot`,
`/opsx-meta-update-index`, `/opsx-meta-commit`, `/opsx-meta-import-command`.

## User input

```text
$ARGUMENTS
```

Optional tokens: `central=<abs-path>`, `repo=<abs-path>` (extra worktree only when
explicit), `HEAD_ONLY_CONFIRMED` (classification hint only until APPLY exists).

Exact `GO` or `GO HEAD_ONLY_CONFIRMED` does **not** authorize writes on TARGET.
Return `FAIL_CLOSED` / `SNAPSHOT_CENTRAL_OVERRIDE_MISSING` without running
`snapshot-apply` for any row.

## Purpose

Keep one catalog-facing snapshot set current when process commands live in
several repos / branch worktrees. Operator uses this command to **inventory**
eligible rows instead of repeating `/opsx-meta-update-snapshot` blindly in each
checkout.

## When to use

- After process commits in **more than one** related worktree (plan which rows
  still need single-repo snapshot publish).
- After discovering two centrals / stale index vs a sibling snapshot.
- Before `/opsx-meta-update-index` when several `SOURCE.yaml` worktrees moved.

## When not to use

- Only the **current** repo changed → `/opsx-meta-update-snapshot`.
- Local process commit → `/opsx-meta-commit`.
- Catalog / occurrences refresh → `/opsx-meta-update-index` (never this command).
- Import a command into this checkout → `/opsx-meta-import-command`.
- First-contact bind → `/opsx-meta-bootstrap`.

## Shared central resolution

Resolve **one** absolute `CENTRAL_ROOT` from the **invoking** repo. On SOURCE,
APPLY passes the same path to every `--repo` as `--central`. TARGET documents
that contract but cannot execute foreign apply until the tool honors `--central`.

Order (first hit wins):

```text
1. argument central=<path>
2. env OPSX_CENTRAL_ROOT
3. invoking-repo opsx/sources.yaml central_root (resolved against invoking cwd)
4. invoking-repo default ../opsx-central
```

If `central=` / env / yaml disagree, STOP:

```text
STATUS: FAIL_CLOSED
REASON: CENTRAL_BIND_CONFLICT
```

Do not invent a second central. Do not copy snapshot trees between centrals.

## Tool gate (`--central`) — TARGET fail closed

Before any APPLY, inspect usage of `opsx_tools/opsx-central-catalog.mjs`.
TARGET usage line (verified at transfer) lacks `--central`:

```text
usage: ... snapshot-plan|snapshot-apply|index-plan|index-apply [--repo <path>] [--head-only]
```

Therefore:

| Mode | TARGET behavior |
|---|---|
| default READ_ONLY | allowed — inventory + optional `snapshot-plan --repo <invoking>` |
| exact `GO` | `FAIL_CLOSED` / `SNAPSHOT_CENTRAL_OVERRIDE_MISSING`; **no** `snapshot-apply` |
| foreign `snapshot-apply` | forbidden until `--central` exists |

```text
NEXT_ACTION: /opsx-meta-fix-command-defects
```

Do not create a central under a sibling worktree's `../opsx-central` when it
differs from resolved `CENTRAL_ROOT`.

## Modes (TARGET)

| Input | Mode | Writes |
|---|---|---|
| default | `READ_ONLY` plan | none |
| exact `GO` | blocked | none (`SNAPSHOT_CENTRAL_OVERRIDE_MISSING`) |
| `HEAD_ONLY_CONFIRMED` + `GO` | blocked | none (same reason) |

SOURCE APPLY semantics (per-row `snapshot-apply --repo … --central …`) are
documented for parity reference only; **not executable** on TARGET today.

## Inventory (every run)

1. Projectization gate (`opsx/projectization.yaml` `COMPLETE`).
2. Resolve `CENTRAL_ROOT` (above). If missing or no `sources/` child, `FAIL_CLOSED`.
3. Enumerate `CENTRAL_ROOT/sources/<family>/<branch>/SOURCE.yaml` only (bounded:
   one Read per `SOURCE.yaml`; no recursive dump of snapshot file bodies).
4. For each row classify exactly one:

| Class | When |
|---|---|
| `LIVE` | `source_repo` is a git worktree; `git rev-parse` works |
| `MISSING_WORKTREE` | path missing, not a git dir, or unreadable |
| `BRANCH_MISMATCH` | live, but `git branch --show-current` ≠ `source_branch` |
| `SKIP_DIRTY` | live + matching branch; allowlisted dirty; no `HEAD_ONLY_CONFIRMED` |
| `PLAN_READY` | live + matching branch; `snapshot-plan` shows publish_eligible |
| `CURRENT` | plan says no safe-tree delta |

`BRANCH_MISMATCH` and `MISSING_WORKTREE` are **not** published. Do not guess
another path. Do not switch branches.

For `PLAN_READY` / `SKIP_DIRTY` / `CURRENT` on the **invoking** repo only, may run:

```powershell
node opsx\tools\opsx-central-catalog.mjs snapshot-plan --repo <source_repo>
```

Never run `snapshot-apply` on TARGET from this command. For foreign worktrees,
classify using git state + SOURCE.yaml; do not apply.

Optional extra worktrees: only if `$ARGUMENTS` lists explicit `repo=<abs-path>`
tokens. Never walk `meta-repo/**` or sibling disks.

## Hard rules

- Copy only committed allowlisted blobs (same as single-repo snapshot) when APPLY
  exists elsewhere; on TARGET, no copy.
- Never copy `.env*`, product trees, `node_modules`, secrets.
- Never mutate catalog index files / `occurrences.ndjson`.
- Never chain `/opsx-meta-update-index` as a side effect.
- Never force-push. Never edit local `.cursor/commands/**` in any worktree from here.

## Output

```text
STATUS: PLAN_READY | FAIL_CLOSED
MODE: READ_ONLY
CENTRAL_ROOT:
TOOL_CENTRAL_OVERRIDE: missing
SOURCE_PARITY: PARTIAL
GO_SUPPORTED: false
ROWS:
- SOURCE_ID:
  SOURCE_REPO:
  SOURCE_BRANCH:
  CLASS: LIVE | MISSING_WORKTREE | BRANCH_MISMATCH | SKIP_DIRTY | PLAN_READY | CURRENT
  SNAPSHOT_DIR:
  RESULT:
INDEX_STATUS: STALE | UNKNOWN
WHAT_HAPPENED:
WHY:
CURRENT_STATE:
OWNER_DECISION_REQUIRED:
NEXT_ACTION:
DO_NOT_DO_YET:
ORIENTATION_HINT:
  LANE: META
  NEXT_META: /opsx-meta
  NEXT_ACTION:
```

### STATUS values (TARGET)

```text
PLAN_READY     inventory complete; no writes; eligible rows listed for manual /opsx-meta-update-snapshot per repo
FAIL_CLOSED    bind conflict, projectization, missing CENTRAL_ROOT, or GO / APPLY attempted without --central
```

### Typical NEXT_ACTION

| STATUS | NEXT_ACTION |
|---|---|
| `PLAN_READY` (eligible rows) | `/opsx-meta-update-snapshot` in each eligible worktree, or `/opsx-meta-fix-command-defects` to add `--central` |
| `FAIL_CLOSED` / tool flag missing | `/opsx-meta-fix-command-defects` |
| after manual per-repo snapshots | `/opsx-meta-update-index` |

`DO_NOT_DO_YET` must include: catalog index from this command; force-push;
import; creating a second central; `snapshot-apply` fan-out on TARGET until
`--central` exists.

## Failure / recovery

| Condition | Behavior |
|---|---|
| Tool lacks `--central` | `FAIL_CLOSED` on any `GO`; READ_ONLY inventory still allowed |
| `CENTRAL_BIND_CONFLICT` | no writes |
| `MISSING_WORKTREE` | list path from SOURCE.yaml; skip |
| `BRANCH_MISMATCH` | skip; do not checkout |
| Dirty without confirm | `SKIP_DIRTY`; other rows continue in inventory |

## Integration gap (honest parity)

| SOURCE capability | TARGET status |
|---|---|
| Fan-out `snapshot-apply --central` | **missing** — tool has no `--central` |
| Per-row partial APPLY continue | **not run** on TARGET |
| `SNAPSHOTS_UPDATED` / `PARTIAL` APPLY statuses | **not emitted** from TARGET GO |
| READ_ONLY `SOURCE.yaml` inventory + classification | **supported** |
| Invoking-repo `snapshot-plan` | **supported** when row is local |

Follow `.cursor/commands/opsx-source-resolution.md` and
`.cursor/commands/opsx-execution-preflight.md` for bounded reads.

## Self-review checklist

- [ ] Single-repo snapshot command not used as fan-out substitute on TARGET
- [ ] Same `CENTRAL_ROOT` documented for future `--central` APPLY
- [ ] Catalog not written
- [ ] Index is a separate Owner GO
- [ ] No branch switch, no force-push, no sibling-disk walk
- [ ] `GO` blocked until `--central` exists on the tool
