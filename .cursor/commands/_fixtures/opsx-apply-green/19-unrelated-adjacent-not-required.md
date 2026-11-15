# Fixture 19 — genuinely unrelated adjacent check is NOT_REQUIRED

## Given

A bounded unit test exists that shares no write surface, relevant existing
surface, named RED test seam, or one-hop `src/utils` / `src/api` import
with the current slice.

## Expected

```text
applicability: NOT_REQUIRED
reason: NO_OVERLAPPING_SEAMS
adjacent_final_status: NOT_REQUIRED
Do not execute the unrelated file as a GREEN adjacent obligation.
```
