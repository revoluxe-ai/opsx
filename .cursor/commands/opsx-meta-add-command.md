---
component_id: opsx.commands.meta_add_command
component_version: 0.2.0
ownership: TARGET_ONLY
runtime_class: ACTIVE_WITH_LIMITS
source_commit: 91311ada29721cfb9aab7a00e1dc4d2a0a8d1982
source_path: .cursor/commands/opsx-meta-add-command.md
required_adapters: ["opsx-lite", "project-generic"]
local_modification_note: "Bounded intake front door; SOURCE autonomous P0–P11 delivery not ported"
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
contract_id: OPSX-WB-META-ADD-COMMAND-01
newline_normalization: none
description: "Validate COMMAND+PURPOSE and route to exactly one meta owner (design, import, or fix); no autonomous delivery or blind commit."
argument-hint: "COMMAND: /opsx-<name> PURPOSE: <capability>"
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

**Contract:** `OPSX-WB-META-ADD-COMMAND-01`
**Runtime class:** `ACTIVE_WITH_LIMITS`
**Precedence:** This override wins for target paths, routing authority, and write boundaries.

```text
IMPLEMENTATION_STATUS: IMPLEMENTED_UNVERIFIED
SOURCE_PARITY: PARTIAL
AUTONOMOUS_DELIVERY_TERMINAL: BLOCKED
NOT_COMMITTED_THIS_RUN: true
```

### Target authority (this command)

- **Reads:** `opsx/projectization.yaml`, `opsx/shared/meta-orientation.md`,
  `opsx/shared/command-discovery.md`, local `git` object database for
  `HEAD:.cursor/commands/<name>.md`, optional Owner donor hints.
- **Writes:** none on the default path (`READ_ONLY` intake and routing only).

### Hard target bans

- Do not install dependencies or modify lockfiles / `package.json`.
- Do not access production / Kubernetes / secrets.
- Do not push, open PR, rebase, force, or deploy.
- Do not write `GRAPH.md`, `META.md`, `openspec/changes/**`, product trees,
  or append activation/provenance — those owners stay separate.
- Do not invoke `/opsx-meta-commit`, `/opsx-pipeline`, or donor merge in the
  same invocation as routing (`META_COMMIT_INVOKER: NO`).
- Do not claim `OPSX_COMMAND_DELIVERY_GREEN` without independent execution
  evidence from the downstream owner chain.
- `/opsx-meta-import-command` remains the **sole** donor import merge writer.
- Cross-project copy from sibling worktrees is forbidden (see cross-project
  import ban rule).

### Projectization gate

Read `opsx/projectization.yaml` before any downstream recommendation.
If `status` != `COMPLETE` or `normal_commands_enabled` is false:

```text
STATUS: FAIL_CLOSED
REASON: OPSX_PROJECTIZATION_REQUIRED
NEXT_COMMAND: /opsx-projectize
```

### Named dependencies

- `.cursor/commands/opsx-execution-preflight.md`
- `.cursor/commands/opsx-source-resolution.md`
- `.cursor/commands/opsx-meta-design-command.md`
- `.cursor/commands/opsx-meta-import-command.md`
- `.cursor/commands/opsx-meta-fix-command-defects.md`
- `.cursor/commands/opsx-meta-commit.md` (terminal owner only; **not** auto-invoked)

---

# /opsx-meta-add-command

Bounded **meta command-delivery front door** for Owner `COMMAND` + `PURPOSE`.
Validates intake, classifies create vs fix vs import, and routes to **exactly
one** existing authority. Does **not** run design, import, fix, pipeline sync,
tests, or commit inside this invocation.

SOURCE at `91311ada…` describes full autonomous `P0_RECON`–`P11_COMMIT`
delivery (`AUTONOMOUS_DELIVERY`). On this target that chain is **not**
executable under current Owner prohibition on automatic `/opsx-meta-commit` and
without SOURCE `vue-npm` / Node mission tooling. This command preserves the
SOURCE **name** and intake contract while blocking the autonomous terminal.

## User input

```text
$ARGUMENTS
```

Required:

```text
COMMAND: /opsx-<name>
PURPOSE: <capability description>
```

Optional: `REQUIRED_BEHAVIOR`, `KNOWN_SEAMS`, `NON_GOALS`,
`COMPATIBILITY_REQUIREMENTS`, `OWNER_CONSTRAINTS`, `DONOR:` / `IMPORT_FROM:`,
`CLASSIFICATION_HINT:` (`LOW` | `STANDARD` | `HIGH` — advisory only; cannot
downgrade safety routing).

`COMMAND` + `PURPOSE` authorize **routing analysis only**, not file writes and
not `/opsx-meta-commit`. Explicit `GO` does not start implementation here.

## Create vs Fix (authoritative)

Shared SoT: `opsx/shared/meta-orientation.md` § Create vs Fix.

| Situation | Owner command |
|---|---|
| New command (path **not** in `HEAD`) | `/opsx-meta-design-command` |
| Uncommitted draft exists (file on disk, **not** in `HEAD`) | `/opsx-meta-design-command` (`RESUME_UNCOMMITTED_COMMAND`) |
| Committed command needs repair / extend | `/opsx-meta-fix-command-defects` |
| Donor / catalog import merge | `/opsx-meta-import-command` only |
| Autonomous end-to-end delivery (SOURCE) | **BLOCKED** on target — use routed owners |

## Intake steps (execute in order)

1. **Validate** `COMMAND` matches `^/opsx-[a-z0-9-]+$` and `PURPOSE` is
   non-empty after trim.
2. **Projectization** gate (above).
3. **Resolve path:** `.cursor/commands/<basename>.md` where basename is the
   slash command without leading `/`.
4. **HEAD probe:** `git cat-file -e HEAD:<path>`
   - success → `ROUTE_CLASS: FIX_COMMITTED` →
     `/opsx-meta-fix-command-defects` with `AUTHORIZE_COMMAND_FIX` only after
     Owner follow-up on that command (not here).
5. **Uncommitted draft probe:** if `Test-Path` / file exists locally and HEAD
   probe failed → `ROUTE_CLASS: RESUME_UNCOMMITTED` →
   `/opsx-meta-design-command` with resume option and the draft path.
6. **Import probe:** if Owner named `DONOR:`, `IMPORT_FROM:`, catalog id, or
   explicit external packet path → `ROUTE_CLASS: DONOR_IMPORT` →
   `/opsx-meta-import-command` (never merge here).
7. **Default:** `ROUTE_CLASS: NEW_COMMAND` → `/opsx-meta-design-command` with
   structured problem statement derived from `PURPOSE`.

Use `opsx/shared/command-discovery.md` before recommending
`NEW_COMMAND` to avoid duplicate central commands (stale index →
`/opsx-meta-update-index` first).

Never route to more than one owner. Never run the selected command in the same
invocation.

## Model tier (handoff only)

Preferred designer tier on downstream design work: **Composer 2.5** (standard)
unless Owner constraints say otherwise. SOURCE preferred **Gemini Flash High** for
design sub-steps — report `MODEL_HANDOFF: downstream_owner` in the routing
packet; do not spawn subagents from this command.

Emergency **GPT-5.6 Sol High** is convergence/authority escalation on downstream
owners only, not this intake path.

## SOURCE parity gaps (explicit)

| SOURCE capability | Target status |
|---|---|
| `OPSX_COMMAND_DELIVERY_GREEN` terminal | `AUTONOMOUS_DELIVERY_TERMINAL: BLOCKED` |
| `P8`–`P11` pipeline audit, sync, commit | Not invoked; `NOT_COMMITTED_THIS_RUN` |
| Registry/provenance append on delivery | `REGISTRY_WRITER: NO` (import/design owners) |
| `tests/unit/opsxMetaAddCommandContract.test.mjs` | Not ported; static transfer tests only |
| Design lock hash tool / META-A..P live suite | Not on intake path |
| `vue-npm` adapter | `UNAVAILABLE` |

Do **not** claim `OPSX_COMMAND_DELIVERY_GREEN` from this command.

## Boundaries

- Default `MODE: READ_ONLY`.
- `REGISTRY_WRITER: NO` | `GRAPH_WRITER: NO` | `META_COMMIT_INVOKER: NO`
- `P11_COMMIT: BLOCKED` here — scoped process commit remains `/opsx-meta-commit`
  after downstream work and explicit Owner GO on that lane.
- Follow `.cursor/commands/opsx-execution-preflight.md` I/O budgets for evidence.

## Output contract

```text
STATUS: ROUTED | BLOCKED_NEEDS_INPUT | FAIL_CLOSED
MODE: READ_ONLY
ROUTE_CLASS: NEW_COMMAND | RESUME_UNCOMMITTED | FIX_COMMITTED | DONOR_IMPORT
COMMAND: /opsx-<name>
PURPOSE: <echo>
TARGET_PATH: .cursor/commands/<name>.md
HEAD_PRESENT: yes | no
WORKTREE_DRAFT: yes | no
SOURCE_PARITY: PARTIAL
AUTONOMOUS_DELIVERY_TERMINAL: BLOCKED
NOT_COMMITTED_THIS_RUN: true
ROUTING_PACKET:
  (pasteable summary for the chosen owner)
WHY:
NEXT_ACTION: /<exact-owner-command> <pasteable-args>
DO_NOT_DO_YET:
- /opsx-meta-commit
- /opsx-pipeline UPDATE GO
- claiming OPSX_COMMAND_DELIVERY_GREEN
- donor merge outside /opsx-meta-import-command
ORIENTATION_HINT:
  LANE: META
  STATUS: ROUTED
  NEXT_META: /opsx-meta
  NEXT_ACTION: <same as NEXT_ACTION>
```

## Failure / recovery

| Condition | STATUS | NEXT |
|---|---|---|
| Missing `COMMAND` or `PURPOSE` | `BLOCKED_NEEDS_INPUT` | resubmit with both |
| Invalid command name | `FAIL_CLOSED` | fix `COMMAND` pattern |
| Ambiguous import vs new | `BLOCKED_NEEDS_INPUT` | one narrow donor question |
| Projectization incomplete | `FAIL_CLOSED` | `/opsx-projectize` |

## Anti-patterns

- Treating this command as autonomous delivery or donor import merge
- Invoking `/opsx-meta-commit` or `/opsx-pipeline` from intake
- Writing activation/provenance rows from the front door
- Claiming `OPSX_COMMAND_DELIVERY_GREEN` without downstream evidence
- Routing to both design and import
- Copying command bodies from sibling projects without
  `/opsx-meta-import-command`
