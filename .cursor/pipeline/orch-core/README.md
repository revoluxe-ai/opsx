# OPSX Orchestration Core (`/opsx-orch-*`)

Shared orchestration contracts for the experimental `/opsx-orch-*` command family.
Separates **HOW** orchestration works from **WHAT** each scenario runs and **WHO**
executes worker roles.

```text
ORCHESTRATOR CORE (OPSX-ORCH-CORE-01)   = HOW orchestration works
SCENARIO (/opsx-orch-bug-fix, …)        = WHAT DAG / gates run
EXECUTOR (Task background, HOST_INLINE) = WHO runs a worker role
```

## Layout

| Path | Contract ID | Role |
|------|-------------|------|
| `CORE.md` | `OPSX-ORCH-CORE-01` | Mission identity, locks, ETA vs SSCA, terminalization |
| `ASYNC_TRANSPORT.md` | `OPSX-ORCH-ASYNC-TRANSPORT-01` | Background Task dispatch, liveness, observability |
| `schemas/mission.schema.json` | — | Durable mission record under `opsx/runs/orch-missions/` |
| `schemas/worker-dispatch.schema.json` | — | Outer worker dispatch / harvest envelope |
| `schemas/owner-aggregate-output.schema.json` | — | Owner-facing compact aggregate block |
| `schemas/evolution-queue-entry.schema.json` | — | Mechanical disposition debt-tail queue item |

## First scenario consumer

- **Command:** `.cursor/commands/opsx-orch-bug-fix.md`
- **Contract:** `OPSX-GAME-ORCH-BUG-FIX-01`
- **Fixtures:** `.cursor/commands/_fixtures/orch-bug-fix/`
- **Tests:** `tests/unit/opsxOrchBugFixContract.test.mjs`, `tests/unit/opsxOrchCoreContract.test.mjs`

## Non-goals

- Does **not** replace `/opsx-supervisor`
- Does **not** own bug classification, disposition truth, or durable bug lifecycle writes
- Does **not** model `/opsx-bug-team` internal waves (WAVE_1–WAVE_4)

## Mission durable root

Approved orchestrator-owned mission state:

```text
opsx/runs/orch-missions/<MISSION_ID>/
  mission.json
  workers/<WORKER_ID>.json
  locks/product-writer.json
  receipts/
  evolution-queue.ndjson
```

Scenario-specific semantics (disposition routing, closure matrix) live in the
scenario command, not in this core.
