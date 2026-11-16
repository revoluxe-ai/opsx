---
component_id: opsx.commands.models
component_version: 0.1.0
ownership: TARGET_ONLY
runtime_class: ACTIVE
contract_id: OPSX-WB-MODELS-01
source_commit: 91311ada29721cfb9aab7a00e1dc4d2a0a8d1982
source_path: .cursor/commands/opsx-models.md
local_modification_note: "SOURCE model-routing contract activated on TARGET after Temporal Cursor SDK runtime migration"
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
required_adapters: ["opsx-lite", "project-generic"]
newline_normalization: none
---


# TARGET OVERRIDE — HIGHEST PRIORITY

**Contract:** `OPSX-WB-MODELS-01`
**Runtime class:** `ACTIVE`

### Purpose boundary

Model configuration **inspection** and **Owner-controlled configuration write**
for Temporal / Cursor Agent model routing only.

**Non-goals:** Temporal redesign, cost accounting, auto tier optimization,
product code edits, PBH/BugBot/Security runs, silent runtime policy sync.

### Projectization gate

Read `opsx/projectization.yaml` before any write to config files.
If `status` != `COMPLETE` or `normal_commands_enabled` is false:

```text
STATUS: FAIL_CLOSED
REASON: OPSX_PROJECTIZATION_REQUIRED
NEXT_COMMAND: /opsx-projectize
```

### Hard bans

- Do not guess technical model IDs from display names (`MODEL_ID_UNKNOWN` when API cannot prove ID).
- Do not write two authority layers in one `GO`.
- Do not substitute unavailable models automatically (`BLOCKER_CLASS: MODEL_NOT_AVAILABLE`).
- Do not emit `MODEL_CONFIG_APPLIED` on file write alone — execution/durable proof rules below apply per scope.
- Do not maintain a hard-coded model catalog as authority (discovery only).

Follow `.cursor/commands/opsx-execution-preflight.md` (Read / tool I/O budgets).

# BEGIN COMMAND BODY

---
name: /opsx-models
id: opsx-models
category: Workflow
description: "READ_ONLY-first inspection of Temporal agent model routing; GO-gated write with scope=effective|project|runtime"
argument-hint: "[scope=effective|project|runtime]"
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


# /opsx-models

```text
IMPLEMENTATION_STATUS: IMPLEMENTED
RUNTIME_STATUS: ACTIVE
SOURCE_PARITY: ADAPTED
GO_SUPPORTED: true
READ_ONLY: true (default)
TEMPORAL_EFFECTIVE_ROUTING: AVAILABLE
TRANSPORT: CURSOR_PYTHON_SDK
```

## User input

```text
$ARGUMENTS
```

Owner-facing control plane for **OPSX Temporal model routing**: discover durable
policy, runtime overlay, effective `/tempo-*` execution mapping, and API-visible
models; propose changes; apply only after explicit **`GO`**.

Resolver SoT: `tools/temporal/model_config.py` (`AgentModelConfig`).

Config paths:

| Layer | Path |
|-------|------|
| Durable project policy | `tools/temporal/agent-models.json` |
| Runtime overlay | `opsx/runs/temporal/config/agent-models.overlay.json` |

Required steps: `review`, `repair`, `next_check`, `verification_executor`.

## When to use

- Before or after `/tempo-*` when model identity or budget is unclear.
- When durable policy and runtime overlay may diverge.
- When tier names (`low` / `medium` / `high`) do not prove `Cursor SDK model_id` slug.

## When not to use

- Product bug fixes, verification runs, or pipeline redesign.
- Bulk cost tuning or automatic “best model” selection.

## Scopes

| Scope | Default? | Write target | Success proof |
|-------|----------|--------------|---------------|
| **`effective`** | **Yes** (plain `/opsx-models`) | Path from `resolve_execution_authority` | `OWNER_REQUESTED_MODEL_ID == ACTUAL_TEMPORAL_EFFECTIVE_MODEL_ID` per required step |
| **`project`** | No (`scope=project`) | `tools/temporal/agent-models.json` only | Durable round-trip matches request; overlay bytes unchanged; **not** a failure if execution unchanged under active overlay |
| **`runtime`** | No (`scope=runtime`) | Overlay file only | Same equality rule as `effective` against tempo execution path |

Never write both layers in one `GO`.

### Execution authority (read-only)

```text
resolve_execution_authority(repo):
  durable_path = tools/temporal/agent-models.json
  overlay_path = opsx/runs/temporal/config/agent-models.overlay.json
  overlay_present = overlay_path.is_file()
  if overlay_present:
    execution_model_authority = runtime_overlay
    effective_config_path = overlay_path
  else:
    execution_model_authority = durable
    effective_config_path = durable_path
```

**`scope=effective` write target:** `effective_config_path` from the above.

**Tempo execution path (VERIFY_EXECUTION):** Use the same `--model-config`
path `tempo_operator` would pass when overlay file exists
(`overlay_path(repo)`); otherwise `DEFAULT_MODEL_CONFIG_PATH` from
`model_config.py`. Load with `AgentModelConfig.load(path)` and
`resolve_step(step).cli_model`.

Optional: if worker state under `.tmp/temporal/**` records `overlay`, emit
`WORKER_STATE_HINT` in SHOW; do not guess paths.

## State machine

```text
DISCOVER → PRESENT → OWNER_GATE → APPLY → VERIFY_FILE → VERIFY_* → COMPLETE
```

- **OWNER_GATE:** corrections loop updates **proposal only** (no disk).
- **GO:** exact token `GO` (case-sensitive) authorizes single-file apply for bound scope.
- **VERIFY_EXECUTION:** required for `effective` and `runtime`; see `project` below.
- No transition to `APPLY` before `GO`.

Failures: `DISCOVER` / `APPLY` / `VERIFY_*` → `OWNER_ATTENTION`;
`WRITE_PERFORMED: NO | ROLLED_BACK` per atomicity rules.

## READ_ONLY first

First invocation (and every invocation until `GO`) must not mutate config because
of drift discovery.

Emit proposal default: current content of the **scope-selected write target**
(`effective` → effective path; `project` → durable; `runtime` → overlay).

Stop at `STATUS: OWNER_GATE` unless applying after `GO`.

## Model API discovery

Do not use display names as authority.

**Contract (implement via `tools/temporal/model_catalog.py` when present):**

```json
{
  "source": "cursor-python-sdk | UNAVAILABLE",
  "observed_at": "<ISO-8601>",
  "models": [
    {
      "display_name": "<string|null>",
      "model_id": "<string|MODEL_ID_UNKNOWN>",
      "available": true,
      "capabilities": {},
      "source": "cursor-python-sdk"
    }
  ],
  "errors": []
}
```

Resolve Owner corrections: exact `model_id` in catalog, or unique display match;
else `OWNER_ATTENTION`. Refresh discovery before `GO` apply.

## Owner correction grammar (minimal)

- `review = <tier>` or `<tier> review = <display or model_id>`
- `all repair = <model>`
- `reasoning <tier> = <token>` (must satisfy tier schema)
- `scope=project|runtime|effective` on invocation (bind for session)

## Atomic write

```text
1. Read pre-bytes + sha256
2. Validate proposal in memory
3. Write <target>.tmp.<pid>
4. AgentModelConfig.load(tmp)
5. os.replace(tmp, target)
6. Scope-appropriate VERIFY_FILE + VERIFY_*
7. On failure before replace completes: delete tmp; target unchanged
8. On failure after replace: restore pre-bytes → ROLLED_BACK
```

## VERIFY — per scope

### `scope=effective` or `scope=runtime`

**Required for `MODEL_CONFIG_APPLIED`:**

For each required step `S`:

```text
OWNER_REQUESTED_MODEL_ID[S] == ACTUAL_TEMPORAL_EFFECTIVE_MODEL_ID[S]
```

where `ACTUAL_*` comes from tempo execution path resolver above.

If write succeeds but equality fails → `BLOCKER_CLASS: EXECUTION_PROOF_FAILED`;
never `MODEL_CONFIG_APPLIED`; rollback when atomic policy requires.

### `scope=project`

Owner requested **durable policy mutation only**.

**Required for `MODEL_CONFIG_APPLIED`:**

1. Written durable file round-trip: each tier/step in proposal matches
   `OWNER_REQUESTED_MODEL_ID` in **durable** file (cli_model per step resolution).
2. Runtime overlay file **unchanged** (byte compare or hash).
3. If overlay is active, actual execution **may** remain unchanged — **not** a failure.
4. **Do not** rollback a valid durable write because overlay still controls execution.

**Forbidden claims:** never state that actual execution changed unless
`EFFECTIVE_EXECUTION_CHANGED: true`.

**Always show** `ACTUAL_TEMPORAL_EFFECTIVE_MODEL_ID` per step (from tempo path).

**Success output (overlay active, execution unchanged):**

```text
STATUS: MODEL_CONFIG_APPLIED
SCOPE: project
DURABLE_POLICY_APPLIED: true
EFFECTIVE_EXECUTION_CHANGED: false
RUNTIME_OVERRIDE: true
```

When overlay absent and durable is execution authority:

```text
EFFECTIVE_EXECUTION_CHANGED: true
RUNTIME_OVERRIDE: false
```

## Drift detection (SHOW)

WARN or FAIL (execution safety) when applicable:

- Durable references model absent from API discovery
- Overlay overrides durable step/tier resolution (`RUNTIME_OVERRIDE`)
- Label disagrees with stored technical id (informational)
- Resolver disagreement vs on-disk JSON

## SHOW output contract

```text
STATUS: OWNER_GATE

MODEL_API_SOURCE:
MODEL_API_OBSERVED_AT:

EXECUTION_MODEL_AUTHORITY: durable | runtime_overlay
EFFECTIVE_CONFIG_PATH: <exact path>

DURABLE_CONFIG: tools/temporal/agent-models.json
RUNTIME_OVERLAY: opsx/runs/temporal/config/agent-models.overlay.json

ACTIVE_SCOPE_DEFAULT: effective
WRITE_TARGET_FOR_CURRENT_SCOPE: <path>

TIER_DEFINITIONS:
TIER   DISPLAY NAME   TECHNICAL MODEL ID (cli_model)
...

STEP_RESOLUTION:
STEP   TIER   DURABLE_ID   OVERLAY_ID   EFFECTIVE_ID

AVAILABLE_MODELS:
...

WARNINGS:
...

PROPOSED_MAPPING:
PROPOSED_DIFF:

NEXT_ACTION:
  Owner corrections or GO.
```

## APPLY success output contract

```text
STATUS: MODEL_CONFIG_APPLIED
SCOPE: effective | project | runtime
EXECUTION_MODEL_AUTHORITY:
EFFECTIVE_CONFIG_PATH:
FILES_CHANGED: [<single path>]

DURABLE_POLICY_APPLIED: true | false
EFFECTIVE_EXECUTION_CHANGED: true | false
RUNTIME_OVERRIDE: true | false

EXECUTION_VERIFICATION:   # effective + runtime scopes
  PASS | N/A
  per-step OWNER_REQUESTED_MODEL_ID, ACTUAL_TEMPORAL_EFFECTIVE_MODEL_ID, MATCH

DURABLE_VERIFICATION:     # project scope
  PASS
  per-step/tier requested vs durable cli_model

ROUND_TRIP_VALIDATION: PASS

APPLIED_MAPPING:
RECEIPT: opsx/runs/temporal/config/receipts/model-config-<utc>.json

NEXT_ACTION: none
```

## Failure output contract

```text
STATUS: OWNER_ATTENTION
BLOCKER_CLASS: MODEL_NOT_AVAILABLE | EXECUTION_PROOF_FAILED | ...
FACTS:
WRITE_PERFORMED: NO | ROLLED_BACK
OWNER_DECISION_REQUIRED:
```

## Evidence receipt

On successful apply, write
`opsx/runs/temporal/config/receipts/model-config-<utc>.json` with:
pre/post hash, scope, API source/timestamp, requested mapping, verification
result, files changed.

## Implementation delegation

Prefer running (when implemented):

```bash
python tools/temporal/opsx_models.py show --repo <repo> --scope effective
python tools/temporal/opsx_models.py apply --repo <repo> --scope <scope> --proposal <file>
```

Until CLI exists, agent may inline the same semantics using `model_config.py`
and bounded reads; missing discovery CLI → `MODEL_API_SOURCE: UNAVAILABLE` and
fail closed on apply if IDs cannot be validated.

Do not modify `package.json` or lockfile from this command.

## Acceptance tests (design suite)

Implement in `tools/temporal/tests/test_opsx_models.py`:

1. SHOW is read-only (config mtime/hash unchanged).
2. Discovery returns exact technical IDs; never infers from labels.
3. Unknown / unavailable model → no write.
4. Owner correction changes proposal only.
5. `GO` performs write; no `GO` → no write.
6. Atomic failure leaves prior config intact.
7. Round-trip durable mismatch → FAIL.
8. Runtime overlay override visible in SHOW.
9. Stale durable model → WARN/FAIL per safety class.
10. Idempotent re-apply same proposal.
11. **Overlay active + plain `/opsx-models` + `GO`:** overlay/effective
    authority changes; durable unchanged; execution proof PASS.
12. **Overlay active + `scope=project` + `GO`:** durable changes; overlay
    unchanged; `MODEL_CONFIG_APPLIED` with `DURABLE_POLICY_APPLIED: true`,
    `EFFECTIVE_EXECUTION_CHANGED: false`, `RUNTIME_OVERRIDE: true`; no rollback.
13. **No overlay + plain `GO`:** durable is effective authority and changes.
14. **Successful file write but execution proof fails** (`effective`/`runtime`):
    FAIL / rollback; never `MODEL_CONFIG_APPLIED`.

## Related commands

- `/tempo-*` — consumes model config via overlay path when present
- `/opsx-meta-consult` — routing when unsure which command to run
- `/opsx-meta-commit` — commit process surfaces after command file changes

## Regression

After implementation touches helpers: run
`node --test` / `python -m unittest` for `test_opsx_models.py` and existing
`test_model_config.py` (no regression in overlay write semantics).

# END COMMAND BODY
