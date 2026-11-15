# Fixture — no outer wave duplication

## Context

Orch admits MUST_FIX work item and dispatches Bug Team.

## Expected outer DAG

```text
OUTER_STAGES_MODELED:
  S4 MUST_FIX_REPAIR (single opaque hop)
NOT_MODELED_AS_OUTER_STAGES:
  WAVE_1 MATERIALIZER
  WAVE_2 CLASSIFIER
  WAVE_3 RED/GREEN
  WAVE_4 FANOUT/LEARNING
OUTER_WAVE_DUPLICATION: FORBIDDEN
BUG_TEAM_OPAQUE: YES
```

## Forbidden

```text
Orch dispatch of internal Bug Team worker roles
Orch SSCA from internal RED/GREEN artifacts without top-level envelope
```
