# Fixture — Bug Team learning does not create Evolution queue

## Context

Bug Team learning packet suggests structural improvements after GREEN.
Disposition receipt for member has `STRUCTURAL_DEBT_TAIL: NO`.

## Expected outer Orch

```text
EVOLUTION_QUEUE_SOURCE: disposition receipts ONLY
LEARNING_PACKET: supporting evidence at most
NEW_TAIL_FROM_LEARNING: FORBIDDEN
ORCH_EDITORIAL_MERGE: FORBIDDEN
QUEUE_ENTRIES_FROM_THIS_MEMBER: 0
```

## Forbidden

```text
add learning-derived tail to evolution-queue.ndjson
merge learning + disposition into one tail
```
