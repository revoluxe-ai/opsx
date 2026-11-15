# OPSX meta orientation

Authoritative meta router: `.cursor/commands/opsx-meta.md`  
Top-level PRODUCT/META entry: `.cursor/commands/opsx.md`

## Lanes

```text
/opsx            → PRODUCT | META detection
/opsx-meta       → command-plane orientation (READ_ONLY)
/opsx-meta-*     → concrete meta workers
```

## Write owners (must not chain)

| Command | Writes |
|---|---|
| `/opsx-meta-bootstrap` | Owner-approved `BOOTSTRAP_PROFILE` seed/bind (exempt from projectization gate) |
| `/opsx-meta-commit` | local process surfaces only |
| `/opsx-meta-update-snapshot` | central `sources/<repo>/<branch>/` Git |
| `/opsx-meta-update-index` | central `catalog/` Git |
| `/opsx-meta-import-command` | selected local command packet + lock/activation/provenance |

## Read-only analysis and lesson workers

| Command | Role | Writes |
|---|---|---|
| `/opsx-meta-consult` | classify a process question and route it | never |
| `/opsx-meta-analyze-pipeline` | analyze pipeline roles, transitions, UX, and improvement options | never |
| `/opsx-meta-promote-process-lesson` | classify a transferable process lesson and emit a fix/design handoff | never |

## Symptom → command → writes (LangGraph)

| Symptom | Command | Writes |
|---------|---------|--------|
| Broken graph/agent/hop, pytest red in `opsx/opsx_runtime` | `/opsx-meta-repair-langgraph` → Owner `GO` apply in chat | `opsx/opsx_runtime/**` |
| Wrong `.cursor` command text / meta handoff | `/opsx-meta-fix-command-defects` | `.cursor/**`, shared meta only |
| Post-session harden meta commands | `/opsx-meta-debrief-chat` → fix-command-defects **only if** plan patches commands | see debrief branch A/B |

**Never** route `opsx/opsx_runtime` repairs to fix-command-defects.

## Lexicon: SDK (meta commands)

In `.cursor/commands/opsx-meta-*.md` and Subagent/Task briefs, **SDK means Cursor SDK
only**: `agents/adapters/cursor_sdk/*`, transport, `GetRun`, stream, `run.wait`,
`SDK_ACTIVE_RUN_CONFLICT`, and `SDK_*` recovery tied to live Cursor runs.

Do **not** call plan hop, `implementation_execute_hop_node`, `hop_lane_status`, or
stagnation **SDK hop** — say **implementation hop**, **execute hop**, **mutating
hop**, or **OPSX implementation lane**. When agents use Cursor: **mutating hop
via Cursor SDK** or **agent.run (Cursor SDK transport)**. Full guard:
`.cursor/commands/opsx-meta-fix-command-defects.md` § Lexicon: SDK.

## Create vs Fix (authoritative)

- Extend an existing command, rule, contract, or shared process authority:
  `/opsx-meta-fix-command-defects`.
- Design a new command or greenfield check only when no existing durable
  authority can be extended: `/opsx-meta-design-command`.
- Analyze pipeline design without applying changes:
  `/opsx-meta-analyze-pipeline`.
- Classify an already evidenced process lesson:
  `/opsx-meta-promote-process-lesson`.
- If the class or owner is unclear: `/opsx-meta-consult`.

Analysis and lesson packets are evidence and handoffs, not write
authorization. A chat-only packet never updates shared authority.

## Bootstrap

Shared profile: `opsx/shared/bootstrap-profile.md`  
First-contact (no `opsx/`): `/opsx-meta-bootstrap` → then `/opsx-projectize`.

## Orientation output

Workers should end with:

```text
ORIENTATION_HINT:
  LANE: META
  STATUS: <token>
  NEXT_META: /opsx-meta
  NEXT_ACTION: <agent dispatch or Owner decision — not Owner shell>
```

**Agent dispatch:** when `NEXT_ACTION` is `/opsx-meta-*`, the **current agent** runs that command (or spawns its Task child) in the same mission — Owner does not paste it into a terminal.

Shared execution boundary: `opsx/shared/owner-agent-execution-boundary.md`

## Discovery

Shared discovery schema: `opsx/shared/command-discovery.md`

## Split-apply recovery (product lane handoff)

When split-aware apply deadlocks on post-implement test-contract evidence
(`TEST_CONTRACT_REBASELINE_REQUIRED`, operative re-RED blocked while tests pass):

```text
WHERE: SPLIT_APPLY_RECOVERY
NEXT_ACTION: /opsx-post-implement-test-contract-recovery change=<change-id> mode=advance
```

Then resume **`/opsx-apply <change-id>`** until split plan reports
`APPLY_COMPLETE`. Meta router table: `/opsx-meta` → `SPLIT_APPLY_RECOVERY`.
