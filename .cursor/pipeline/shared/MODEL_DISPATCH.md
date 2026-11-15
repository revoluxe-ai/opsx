# Model dispatch policy (OPSX process surfaces)

```text
POLICY_ID: OPSX_MODEL_DISPATCH_DEFAULT_EXPLICIT_V2
STATUS: ACTIVE
OWNER_RULE: .cursor/rules/subagent-model-policy.mdc
MODEL_REGISTRY_PATH: .cursor/pipeline/shared/MODEL_REGISTRY.json
SYNC_COMMAND: /opsx-meta-sync-models
```

## Default rule (Owner 2026-10-23)

Authority: **`.cursor/rules/subagent-model-policy.mdc`** (alwaysApply). This file
implements the same **default** policy for OPSX orchestrators.

When a command, worker registry entry, pipeline config (e.g.
`tools/temporal/agent-models.json` tier mapping), or Owner **explicitly**
names a Task/worker model (must not be a `*-fast` slug — see hard ban below) —
use that slug. That is compliant.

When **no** such explicit specification exists for a hop:

- pass an **explicit** full standard `model` slug on every Task / subagent / worker launch;
- **do not** omit `model`, use `inherit`, or `auto` (silent parent model / platform Auto);
- **do not** rely on UI or tool defaults (bare “Composer 2.5” in UI may select Fast);
- default: **Composer 2.5 Standard** (`composer-2.5`) — never `composer-2.5-fast`;
- super-complex hops only: **Grok 4.7 Medium** (non-fast), slug `grok-4.7-medium` — never `grok-4.7-medium-fast` (architecture, concurrency/authority review, hard debugging).

## Hard ban: `*-fast` (absolute)

Any **`*-fast`** model (Composer Fast, `composer-2.5-fast`, `grok-*-fast`,
`gpt-*-fast`, etc.) is **forbidden** for chats, workers, and subagents.

This ban is **not** waived by “explicit model elsewhere is OK”. The only
exception is when Owner **explicitly lifts** the fast ban.

## Task tool shape (when command/registry does not name a model)

```text
Tool: Task
arguments:
  description: "<short title>"
  subagent_type: <from registry or generalPurpose>
  model: composer-2.5
  prompt: |
    ...
    MODEL_SELECTION_POLICY: default-explicit (Composer 2.5 Standard | Grok 4.7 Medium)
```

Use slug `composer-2.5` for Composer 2.5 Standard. For super-complex hops only,
use `model: grok-4.7-medium` (Grok 4.7 Medium, non-fast).

When a consumer command or registry **does** specify `model` (non-fast
allowlisted slug), pass that value explicitly — do not replace with default.

## Pseudo tokens (registry)

| Token | Meaning |
|---|---|
| `inherit` | Parent chat model — **avoid** unless a command/registry **requires** `inherit` as the contract token; never use as silent default when `model` is omitted |
| `auto` | Platform Auto — **not** a default when unspecified |

API slugs live in `MODEL_REGISTRY.json`. Owner `MODEL=<slug>` on orchestrator
args is valid when the command contract defines it; slug must match registry
when validated via `/opsx-meta-sync-models`. `*-fast` slugs remain forbidden
unless Owner lifts the ban.

## Orchestrator dispatch envelope

```text
MODEL_MODE: EXPLICIT_DEFAULT | EXPLICIT_COMMAND
REQUESTED_MODEL: <non-fast slug from command/config> | composer-2.5 | grok-4.7-medium
MODEL_SELECTION_REASON: OPSX_MODEL_DISPATCH_DEFAULT_EXPLICIT_V2 + subagent-model-policy.mdc
```

## Consumers

- `/opsx-supervisor` §7 / §15
- `/opsx-commit-all`
- `/opsx-bug-team` + bug-team pipeline (`TASK_DISPATCH`, `REGISTRY`, workers)
- Future: `/opsx-static-check` SERIAL workers (separate fix-defects packet)

## Violations

## Implementation lane exception (Owner 2026-11-10)

| Hop | Model slug | Notes |
|-----|------------|-------|
| `implementation_arbiter` | `claude-sonnet-5-5` (context 1m, reasoning high) | Explicit Owner request; all other split-apply hops remain `composer-2.5` via `IMPLEMENTATION_ARBITER_MODEL` / `default_hop_model_selection()`. |

| Observation | Verdict |
|---|---|
| `model` omitted on Task when command/registry/config does not authorize omit | `POLICY_VIOLATION` |
| Relying on UI/tool default instead of full standard slug | `POLICY_VIOLATION` |
| `model: inherit` or `model: auto` used as default where no contract requires it | `POLICY_VIOLATION` |
| Any `*-fast` slug (no exception from command/config; Owner lift only) | `POLICY_VIOLATION` |
| Explicit non-fast slug from command, registry, pipeline config, or Owner | compliant |
| Default Composer 2.5 Standard (`composer-2.5`) or Grok 4.7 Medium (`grok-4.7-medium`) when nothing else specified | compliant |
