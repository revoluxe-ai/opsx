# Fixture — project-bug-hunt NEXT_ACTION not followed by Orch v1

## Context

Bug Team top-level output includes pasteable:

```text
NEXT_ACTION: /opsx-project-bug-hunt base=… head=…
```

Mission batch otherwise complete.

## Expected outer Orch (S11)

```text
AUTO_PROJECT_BUG_HUNT: NO
RETURN_TO_CALLER_DISCOVERY_GATE: YES
BUG_BATCH_CLEAR: YES (when mission predicate satisfied)
RECURSIVE_DISCOVERY_IN_V1: FORBIDDEN
```

## Forbidden

```text
automatically dispatch /opsx-project-bug-hunt because Bug Team said so
start new discovery cycle inside same mission
```
