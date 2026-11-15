---
description: "Bugbot convergence via Temporal/Cursor SDK until native CLEAR or bounded stop."
argument-hint: "against=<target> review=<tier> repair=<tier>"
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

# /opsx-orch-bugbot-converge

TRANSFER_STATE: FUNCTIONAL_PROVEN
IMPLEMENTATION_STATUS: FUNCTIONAL_PROVEN
SOURCE_PARITY: PARTIAL
SUPERVISOR_REPLACEMENT: NO
OWNER_GO_AFTER_INVOCATION_REQUIRED: NO
CHECK_COMMAND: /review-bugbot
CHECK_TRANSPORT: TEMPORAL_CURSOR_SDK
REPAIR_PRODUCT: /opsx-orch-bug-fix
WRAPPER_PRODUCT_WRITE: NO
GIT_COMMIT: FORBIDDEN
GIT_PUSH: FORBIDDEN
contract_id: OPSX-WB-ORCH-BUGBOT-CONVERGE-01

```text
component_id: opsx.commands.orch_bugbot_converge
ownership: TARGET_ONLY
runtime_class: ACTIVE
required_adapters: ["opsx-lite", "project-generic"]
```

Shared runtime contract: `OPSX-TARGET-ORCH-RUNTIME-01` (`.cursor/pipeline/contracts/ORCH_TARGET_RUNTIME.md`).
Adapter: `.cursor/pipeline/orch-bugbot-converge/orch_bugbot_converge.py`
Tests: `.cursor/pipeline/orch-bugbot-converge-tests/test_orch_bugbot_converge.py`
Plan helper: `python tools/temporal/orch_target.py converge-plan`

Target realization uses the active Temporal/Cursor SDK bugbot pipeline.
SOURCE Task/TASK_BACKGROUND transport is not retained.

## Arguments

```text
$ARGUMENTS
/opsx-orch-bugbot-converge against=<target> review=<low|medium|high> repair=<low|medium|high>
```

`against=<target>` defaults to stage if omitted only after MODEL_TIER_REQUIRED is satisfied.
review and repair are required. Missing model tier -> BLOCKED, REASON: MODEL_TIER_REQUIRED.
Explicit incompatible SOURCE max_cycles override -> BLOCKED_UNSUPPORTED_BOUND_OVERRIDE; never silently reinterpret it.
`python tools/temporal/orch_target.py converge-plan --args "<$ARGUMENTS>"` is the machine parse for against=/max_cycles=/mission=.

## Execution

Run exactly one canonical entry:

```text
python tools/temporal/runtime.py run bugbot --repo <workspace> --base <against> --review <review> --repair <repair>
```

Do not invoke legacy agent CLI. Do not manually start a second worker.
runtime.py owns runtime bootstrap, singleton pipeline lock, Temporal server, worker, workflow, Cursor SDK route, durable result, and process recovery.
The Temporal bugbot lane owns native Bugbot review, repair through /opsx-orch-bug-fix, fresh re-review, bounded convergence, and no-progress/stall detection.

Product mutation is `/opsx-bug-team` inside `/opsx-orch-bug-fix`. Process remainder is `/opsx-meta-fix-command-defects` (`PROCESS_HANDOFF`). Do not claim `BUGBOT_CONVERGED` while process findings remain.

## Authority

```text
DIRECT_PRODUCT_WRITE=NO
DIRECT_GIT_WRITE=NO
CHECK_AUTHORITY=Temporal bugbot review activity
REPAIR_AUTHORITY=/opsx-orch-bug-fix
MODEL_ROUTE_AUTHORITY=explicit review/repair tier
OWNER_WAKE_ON_DETERMINISTIC_RECHECK=NO
```

## Success evidence

BUGBOT_CONVERGED may be emitted only when the current Temporal workflow terminates with BUGBOT_CLEAN and its durable result file exists for this invocation.
Outcome mapping:
- BUGBOT_CLEAN -> BUGBOT_CONVERGED.
- NO_REPAIR_PROGRESS -> NO_REPAIR_PROGRESS / BLOCKED_NO_PROGRESS
- STALLED -> STALLED / BLOCKED_NO_PROGRESS
- MAX_ROUNDS_REACHED -> ITERATION_LIMIT_REACHED
- activity/process/authority failure -> BLOCKED_* or FAIL_CLOSED

Never infer convergence from BUG_BATCH_CLEAR alone.

## Output

```text
STATUS: BUGBOT_CONVERGED | NO_REPAIR_PROGRESS | STALLED | ITERATION_LIMIT_REACHED | BLOCKED_* | FAIL_CLOSED
COMMAND: /opsx-orch-bugbot-converge
WORKFLOW_ID: <id>
RESULT_FILE: <path>
FINAL_BUGBOT_RESULT: CLEAR | FINDINGS | BLOCKED
BUGBOT_CONVERGED: YES|NO
EVIDENCE_BOUND: YES|NO
NEXT_ACTION: <none|deterministic recovery|exact Owner decision>
```
