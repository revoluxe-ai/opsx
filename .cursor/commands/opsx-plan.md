---
component_id: opsx.commands.plan
component_version: 0.5.0
ownership: TARGET_ONLY
runtime_class: ACTIVE_WITH_LIMITS
source_commit: 91311ada29721cfb9aab7a00e1dc4d2a0a8d1982
source_path: .cursor/commands/opsx-plan.md
required_adapters: ["opsx-lite", "project-generic"]
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
contract_id: OPSX-WB-PLAN-BRIDGE-01
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


# /opsx-plan

Read-only compatibility bridge for the SOURCE command name. Planning work is
owned by the target lifecycle commands; this bridge chooses exactly one and
does not create or edit artifacts itself.

## User input

```text
$ARGUMENTS
```

Optional `change=<id>` or free-text feature intent may disambiguate the route.
Do not treat `GO` as write authorization.

## Routing

1. Resolve the target OpenSpec status and paths using the same status interface
   as `/opsx-new`, `/opsx-ff`, and `/opsx-continue`.
2. Choose exactly one:

| Evidence | Next action |
|---|---|
| No existing change; user supplied new feature/fix intent | `/opsx-new <intent>` |
| Existing change lacks implementation-ready planning artifacts | `/opsx-ff <change>` |
| Existing change has a next schema artifact to create | `/opsx-continue <change>` |
| Existing planning artifacts need revision/coherence repair | `/opsx-update <change>` |
| Change selection is ambiguous | ask one narrow `change=` question |

Never route to obsolete `/opsx-propose`. Do not run the selected command in
the same invocation.

## Boundaries

- Always `READ_ONLY`.
- Follow `.cursor/commands/opsx-source-resolution.md` and
  `.cursor/commands/opsx-execution-preflight.md`.
- Do not edit planning artifacts, source code, tests, commands, or registries.
- Do not claim implementation readiness; the selected lifecycle command owns
  its own gates and output.

## Output

```text
STATUS: ROUTED | BLOCKED_NEEDS_CHANGE | BLOCKED_NEEDS_INTENT
MODE: READ_ONLY
CHANGE:
CURRENT_STATE:
WHY:
NEXT_ACTION:
DO_NOT_DO_YET:
- editing planning artifacts from /opsx-plan
- running multiple lifecycle writers
- /opsx-propose
```
