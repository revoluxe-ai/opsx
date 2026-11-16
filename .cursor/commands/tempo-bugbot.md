---
description: "Temporal operator: bugbot review→orch. Owner runs slash command only."
argument-hint: "[base=stage] <review-tier> <repair-tier>"
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


# /tempo-bugbot

**Owner runs only this slash command.** No manual `python`, no separate worker
terminal, no Temporal UI steps for Owner.

You (the agent) own the full process: Temporal, worker, workflow, monitor,
process repair. Owner does not run `tempo_operator.py` — you do.

Named kernel: `.cursor/commands/_shared/tempo-pipeline.md`

## Owner usage

```text
/tempo-bugbot [base=stage] <review-tier> <repair-tier>
```

| Position | Token | Rule |
|----------|--------|------|
| optional | `base=<branch>` | default `stage` if omitted |
| last | `<review-tier>` | `low` \| `medium` \| `high` — review model |
| last | `<repair-tier>` | `low` \| `medium` \| `high` — orch model |

No `change=` — bugbot reviews the current branch diff against `base`.
`head` is never passed — always current git branch.

Examples:

```text
/tempo-bugbot low low
/tempo-bugbot base=main medium low
```

## Agent execute (single entry — mandatory)

Parse `$ARGUMENTS` (models = **last two tokens**), then run:

```bash
python tools/temporal/runtime.py run bugbot \
  --repo <workspace> \
  [--base stage] \
  --review <review> \
  --repair <repair>
```

This alone: Temporal → worker → workflow → wait → report.

Do **not** ask Owner to run `worker.py`, `start.py`, or other python steps.

## Owner report (when done)

```text
STATUS: PASS | FAIL | BLOCKED
PIPELINE: bugbot
BASE: ...
HEAD: ...
REVIEW_TIER: ...
REPAIR_TIER: ...
WORKFLOW_ID: ...
TEMPORAL_UI: ...
OUTCOME: ...
```

If review activity reports `git_preflight_ok: false`, that is **not** a process
failure — note it in the report; the workflow may still complete.

## Do not

- Do not pass `change=` or `head=` from Owner args.
- Do not tell Owner to run python/temporal commands.
- Do not review or patch product code yourself.
