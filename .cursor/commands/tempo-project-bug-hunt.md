---
description: "Temporal operator: project-bug-hunt (hunt→orch). Owner runs slash command only."
argument-hint: "change=<id> [base=stage] <review-tier> <repair-tier>"
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

# /tempo-project-bug-hunt

**Owner runs only this slash command.** No manual `python`, no separate worker
terminal, no Temporal UI steps for Owner.

You (the agent) own the full process: Temporal, worker, workflow, monitor,
process repair. Owner does not run `tempo_operator.py` — you do.

Named kernel: `.cursor/commands/_shared/tempo-pipeline.md`

## Owner usage

```text
/tempo-project-bug-hunt change=<id> [base=stage] <review-tier> <repair-tier>
```

| Position | Token | Rule |
|----------|--------|------|
| first keys | `change=<id>` | **required** OpenSpec change id |
| optional | `base=<branch>` | default `stage` if omitted |
| last | `<review-tier>` | `low` \| `medium` \| `high` — hunt model |
| last | `<repair-tier>` | `low` \| `medium` \| `high` — orch model |

`head` is never passed — always current git branch.

If `change=` is missing → **ask once**, then stop with `STATUS: BLOCKED`.

Examples:

```text
/tempo-project-bug-hunt change=cross-device-novel-save-refresh low low
/tempo-project-bug-hunt change=my-change base=main medium low
```

## Agent execute (single entry — mandatory)

Parse `$ARGUMENTS` (models = **last two tokens**), then run:

```bash
python tools/temporal/runtime.py run project-bug-hunt \
  --repo <workspace> \
  --change <id> \
  [--base stage] \
  --review <review> \
  --repair <repair>
```

This alone: Temporal → worker → workflow → wait → report.

Do **not** ask Owner to run `worker.py`, `start.py`, or other python steps.

## Owner report (when done)

```text
STATUS: PASS | FAIL | BLOCKED
PIPELINE: project-bug-hunt
CHANGE: ...
BASE: ...
HEAD: ...
REVIEW_TIER: ...
REPAIR_TIER: ...
WORKFLOW_ID: ...
TEMPORAL_UI: ...
OUTCOME: ...
```

## Do not

- Do not put model tiers before `change=`.
- Do not tell Owner to run python/temporal commands.
- Do not hunt or patch product code yourself.
