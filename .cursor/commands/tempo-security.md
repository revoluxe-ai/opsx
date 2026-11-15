---
description: "Temporal operator: security review→bug-team. Owner runs slash command only."
argument-hint: "[base=stage] <review-tier> <repair-tier>"
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

# /tempo-security

**Owner runs only this slash command.** No manual `python`, no separate worker
terminal, no Temporal UI steps for Owner.

You (the agent) own the full process: Temporal, worker, workflow, monitor,
process repair. Owner does not run `tempo_operator.py` — you do.

Named kernel: `.cursor/commands/_shared/tempo-pipeline.md`

## Owner usage

```text
/tempo-security [base=stage] <review-tier> <repair-tier>
```

| Position | Token | Rule |
|----------|--------|------|
| optional | `base=<branch>` | default `stage` if omitted |
| last | `<review-tier>` | `low` \| `medium` \| `high` — review model |
| last | `<repair-tier>` | `low` \| `medium` \| `high` — bug-team model |

No `change=` — security reviews the current branch diff against `base`,
scoped to `app/**` and `tests/**` only (process/openspec paths excluded).
`head` is never passed — always current git branch.

Examples:

```text
/tempo-security low low
/tempo-security base=main medium low
```

## Agent execute (single entry — mandatory)

Parse `$ARGUMENTS` (models = **last two tokens**), then run:

```bash
python tools/temporal/runtime.py run security \
  --repo <workspace> \
  [--base stage] \
  --review <review> \
  --repair <repair>
```

This alone: Temporal → worker → workflow → wait → report.

Do **not** ask Owner to run `worker.py`, `start.py`, or other python steps.

## Cycle (agent-internal)

```text
POC scoped git preflight (app/tests vs base) → PREFLIGHT_ARTIFACT
/review-security  (Read artifact first when POC_GIT_PREFLIGHT: VERIFIED;
                   Base Branch = base; FALLBACK from artifact if subagent
                   cannot compute branch-diff — do not REVIEW_FAILED on that alone)
→ if only process/OPSX findings remain: SECURITY_CLEAN + report (no repair)
→ /opsx-bug-team on app/tests table from immediate prior review
→ /review-security
→ …
```

Review scope is `app/**` and `tests/**` only. Process-lane rows
(`opsx/**`, `.cursor/**`, `tools/**`, `openspec/**`) are excluded before
repair and from the scoped git preflight artifact. When preflight is verified,
the review agent must not recompute against `origin/HEAD` / deleted remotes.

## Owner report (when done)

```text
STATUS: PASS | FAIL | BLOCKED
PIPELINE: security
BASE: ...
HEAD: ...
REVIEW_TIER: ...
REPAIR_TIER: ...
WORKFLOW_ID: ...
TEMPORAL_UI: ...
OUTCOME: ...
EXCLUDED_PROCESS_FINDINGS: ...   (when applicable)
REPAIR: /opsx-bug-team | skipped
```

## Do not

- Do not pass `change=` or `head=` from Owner args.
- Do not tell Owner to run python/temporal commands.
- Do not review or patch product code yourself.
- Do not invoke `/opsx-orch-bug-fix` or `/opsx-security-review` from this pipeline.
