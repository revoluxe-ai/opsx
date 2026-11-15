# Fixture 05 — forbidden behavior

SCAFFOLD must not implement frozen business behavior:

dedupe, retry, authorization, persistence, debounce, API orchestration,
cache merge, error recovery, state-machine transitions, user-visible
behavior, cross-device semantics.

A new function may throw or return `OPSX_SCAFFOLD_NOT_IMPLEMENTED`.
