---
name: /super-implement
id: super-implement
category: Workflow
description: Sequencer over native split-APPLY stages (V0, plan-first)
---

# /super-implement

Thin controller over the existing split-APPLY primitives. It does **not**
merge or replace `/opsx-apply-design` … `/opsx-apply-green`.

## V0 scope

- Deterministic next hop via `selectNextPipelineStage` (`opsx_tools/opsx-apply-pipeline-identity.mjs`)
- Operative receipt checks (`artifact_status` + freshness helpers); **exit code is not proof**
- Durable run ledger: `<changeRoot>/super-implement/RUN.json`
- APPLY_COMPLETE gate via `opsx_tools/opsx-super-implement.mjs` + verify-completion projection
- **Live dispatch is fail-closed** until Owner attestation sets `live_execution_authorized`

## Usage

```bash
node opsx_tools/opsx-super-implement.mjs plan --change-root openspec/changes/<id>
node opsx_tools/opsx-super-implement.mjs status --change-root openspec/changes/<id>
node opsx_tools/opsx-super-implement.mjs advance --change-root openspec/changes/<id>
node opsx_tools/opsx-super-implement.mjs adopt --change-root openspec/changes/<id>
node opsx_tools/opsx-super-implement.mjs run --change-root openspec/changes/<id> \
  --adapter recording --max-hops 64

V0: legacy `createCursorSdkExecutorAdapter` live transport remains **hard-disabled** (`LIVE_SDK_TRANSPORT_DISABLED_V0`).

V1: `run --adapter native-tool|cursor-sdk` requires Owner attestation file, TARGET schemas, and loop-level `LIVE_EXECUTION_BLOCKED` clearance. Real SDK subprocess needs `SUPER_IMPLEMENT_V1_LIVE_SDK_TRANSPORT_ENABLED=true` and `CURSOR_API_KEY` (never logged). Acceptance path: `--adapter recording` or `--adapter native-tool` with fixtures.
```

Chat form (plan-only until live GO):

```text
/super-implement change=<id>
```

## Forbidden substitutes

Never use `/opsx-apply`, `/opsx-verify`, or `/opsx-archive` as a stage hop.

## Non-goals (V0)

- No Temporal workflow
- No auto `/opsx-verify`, merge, deploy, or archive
- No product Change execution without `live_execution_authorized: true`

## V0 safety boundary (stage with live OFF)

**Supported:** plan/status/adopt/run (recording), mid-process adoption, native stage selection, RUN.json reconciliation, fail-closed live gate.

**Not proven:** unattended live SDK dispatch, OS-level write isolation, cross-worktree writer exclusion, guarded receipt writes for agent-direct JSON edits (see `opsx_tools/opsx-apply-change-writer.mjs`).

Manual split-APPLY tool writes use ephemeral compat lease when uncontested; explicit `--writer-session` when a lock is already held.

## Tool authority

Implementation: `opsx_tools/opsx-super-implement.mjs`  
Schema: `opsx_tools/registry/super_implement_run.schema.json`  
Runtime contract: `.cursor/pipeline/contracts/SPLIT_APPLY_TARGET_RUNTIME.md`
