---
component_id: opsx.commands.meta_promote_process_lesson
component_version: 0.5.0
ownership: TARGET_ONLY
runtime_class: ACTIVE
source_commit: 91311ada29721cfb9aab7a00e1dc4d2a0a8d1982
source_path: .cursor/commands/opsx-meta-promote-process-lesson.md
required_adapters: ["opsx-lite", "project-generic"]
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
contract_id: OPSX-WB-META-PROMOTE-PROCESS-LESSON-01
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

This target-native command preserves the SOURCE name and interface. It uses
WB's `/opsx-meta-*` surfaces and is read-only in every mode.

Authority:

- `opsx/shared/meta-orientation.md` owns create-versus-fix routing and shared
  process-surface orientation.
- `/opsx-meta-fix-command-defects` owns changes to existing process authority.
- `/opsx-meta-design-command` owns new command/check design.
- Product logic remains in `/opsx-fix-bug` and `/opsx-learn-from-bug`.
- Typing remains in `/opsx-fix-types` and static checks.

# /opsx-meta-promote-process-lesson

Classify a transferable command/process lesson after a process Bugbot finding
or `/opsx-meta-fix-command-defects` packet, then emit one pasteable target
handoff. This command never applies the lesson.

**After a full meta/LangGraph work session** with repeated Owner architecture
corrections, prefer **`/opsx-meta-debrief-chat`** (chat archaeology +
`COMMAND_PATCH_PLAN` for `opsx-meta-*` chain). Use this command for a **single**
classified lesson from a formal finding or fix packet.

## User input

```text
$ARGUMENTS
```

Input is optional when the current chat already contains the process finding
or `FIXED`/`FIX_PLAN_READY` packet. Exact `GO` does not authorize writes here.

## Modes

| Input | Mode | Writes |
|---|---|---|
| default | `READ_ONLY` | never |
| exact `GO` | `READ_ONLY` | never; report that the routed command owns APPLY |

Within an active `/opsx-supervisor` `AUTHORIZE_META_FIX_SESSION`, this is the
expected read-only lesson hop after a meta-fix reaches
`READY_FOR_COMMAND_DIFF_REVIEW`. It still does not write or advance another
frozen defect itself.

## Branch gate

Accept only command/process evidence: `.cursor/commands/**`, rules, skills,
static-check contracts, pipeline contracts, and OPSX administration.

- Typing evidence: `WRONG_BRANCH`; route to `/opsx-fix-types` or static check.
- Product behavior: `WRONG_BRANCH`; route to `/opsx-fix-bug`.
- Unclear evidence: ask one extraction question and return
  `BLOCKED_NEEDS_EVIDENCE`.

## Disposition

Choose exactly one:

| Disposition | Meaning | Next |
|---|---|---|
| `no_process_lesson` | one-off fix without transferable class | `none` |
| `extend_existing` | existing command/rule/contract needs a guardrail | `/opsx-meta-fix-command-defects` |
| `design_brief_required` | a new command or greenfield check is required | `/opsx-meta-design-command` |
| `needs_more_evidence` | evidence cannot establish the class | one question |
| `wrong_branch` | typing or product behavior | owning non-process command |

Use `extend_existing` whenever a durable authority already exists. A request
to extend a command is not a reason to design a new command.

## Procedure

1. Read only the supplied evidence and exact authorities it cites, under
   `.cursor/commands/opsx-execution-preflight.md` budgets.
2. Extract `PROCESS_CLASS`, `PROCESS_LESSON`, and exact `EVIDENCE_REFS`.
3. Select one disposition.
4. For `extend_existing`, emit a pasteable `FIX_PLAN_HINT` containing exact
   files, current behavior, and proposed behavior.
5. If the lesson changes role boundaries, create-versus-fix, write ownership,
   or a meta front door, include:

   ```text
   SHARED_SURFACES_TO_UPDATE:
   - opsx/shared/meta-orientation.md
   - <other exact shared authority when evidenced>
   ```

6. For `design_brief_required`, explain why no durable authority can be
   extended and emit a `DESIGN_BRIEF_READY` limited to `new_command` or
   `new_check_rule`.
7. Do not edit any file.

## Hard boundaries

Never edit production, tests, OpenSpec product artifacts, commands, rules,
skills, or shared process files. Never auto-create a command, create a
worklog/learning hierarchy, use product learning for process defects, or
claim shared authority changed from a chat-only brief.

## Output contract

```text
STATUS: EXTEND_EXISTING | DESIGN_BRIEF_READY | NO_PROCESS_LESSON |
        BLOCKED_NEEDS_EVIDENCE | WRONG_BRANCH
MODE: READ_ONLY
BRANCH: process_commands
PROCESS_CLASS:
PROCESS_LESSON:
DISPOSITION:
EVIDENCE_REFS:
SHARED_SURFACES_TO_UPDATE:
FIX_PLAN_HINT:
# present only for EXTEND_EXISTING
DESIGN_BRIEF:
# present only for DESIGN_BRIEF_READY; new_command/new_check_rule only
WHAT_HAPPENED:
WHY:
OWNER_DECISION_REQUIRED:
NEXT_ACTION:
DO_NOT_DO_YET:
- /opsx-learn-from-bug for a process lesson
- /opsx-bug-hunt for command-contract classification
- creating or editing process files from this command
```

## Self-review

- Process-only branch enforced.
- Existing authority was extended before any greenfield design.
- Shared-surface obligations are explicit where ownership changes.
- Exactly one disposition and one pasteable next action are present.
- No file write or implied authorization occurred.
