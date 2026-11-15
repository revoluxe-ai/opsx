# Fixture — three distinct disposition debt tails preserved exactly

## Context

One family plan with three members; disposition receipt lists three separate tails:

```text
MEMBER: M1 — MUST_FIX + DEBT_TAIL (balance SoT duplication)
MEMBER: M2 — MUST_FIX + DEBT_TAIL (hydrate reject propagation)
MEMBER: M3 — MUST_FIX + DEBT_TAIL (inventory PUT error surfacing)
```

Persisted in `dispositions/DISP-<PLAN>-<WI>.json` with three distinct
`DEBT_TAIL.problem` strings.

## Expected EVOLUTION_QUEUE

```text
QUEUE_ENTRIES: 3
ORCH_MERGE: FORBIDDEN
ENTRY_1.provenance.disposition_receipt_path: dispositions/DISP-...
ENTRY_2: separate exact payload
ENTRY_3: separate exact payload
EVOLUTION_DEDUPE_OWNER: /opsx-evolution
```

## Forbidden

```text
collapse to "one broader audit"
collapse to two tails
summarize tails in Orch prose
create tails from Bug Team learning
```
