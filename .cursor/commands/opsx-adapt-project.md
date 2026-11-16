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


# TARGET OVERRIDE — HIGHEST PRIORITY

Target-only generated command. No canonical source body.
Must not touch changes/archive/decisions; no dependency installs.

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
