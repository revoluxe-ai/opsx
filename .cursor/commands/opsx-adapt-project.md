---
component_id: opsx.commands.adapt_project
component_version: 0.5.0
runtime_class: ACTIVE_WITH_LIMITS
ownership: TARGET_ONLY
contract_id: OPSX-WB-ADAPT-PROJECT-01
canonical_source_body: none
required_adapters: ["opsx-lite", "project-generic"]
local_modification_note: "v0.5 poetry-python adapter path; Owner projectize GO 2026-08-07"
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

Target-only generated command. No canonical source body.
Must not touch changes/archive/decisions; no dependency installs.

# /opsx-adapt-project

Lightweight repeatable adapter refresh after projectization.

## Runtime class

`ACTIVE_WITH_LIMITS`

## Projectization gate

Before any write, read `opsx/projectization.yaml`.

If `status` is not `COMPLETE` or `normal_commands_enabled` is false:

```text
STATUS: FAIL_CLOSED
REASON: OPSX_PROJECTIZATION_REQUIRED
NEXT_COMMAND: /opsx-projectize
```

Do not mutate planning artifacts or code until projectization is COMPLETE.
`/opsx-projectize` and `/opsx-projectize:GO` are exempt from this gate.

## `/opsx-adapt-project` (read-only)

Detect current capabilities from `pyproject.toml` / Poetry scripts,
`app/` layout, and `opsx/adapters/poetry-python/`. Report activation deltas.
No writes.

## `/opsx-adapt-project:GO`

Updates only:

- `opsx/adapters/poetry-python/`
- `opsx/project/quality-gates.md` / context snippets (if present)
- `opsx_tools/registry/command_activation.ndjson` statuses for capability-bound commands

Does **not** touch `openspec/changes/**`, archive, or decisions.
Does **not** install dependencies or modify application code.
Does **not** inventory or merge foreign OPSX command/policy bodies from another
repo or archive — that is `/opsx-meta-import-command`. After an inbound
merge that touched activation, this command may refresh adapters/gates.

## When not to use

- Foreign OPSX compare / selective merge → `/opsx-meta-import-command`
- First-time projectization → `/opsx-projectize`
- Change delta specs → main → `/opsx-sync`

Unavailable specialized checks remain fail-closed; never invent PASS.
