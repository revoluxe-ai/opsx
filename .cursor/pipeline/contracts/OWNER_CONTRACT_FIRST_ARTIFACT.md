# Owner Contract — first planning artifact (OPSX)

**Contract ID:** OPSX-OWNER-CONTRACT-FIRST-01  
**Consumers:** `/opsx-continue`, `/opsx-ff`, skill mirrors `openspec-continue-change`, `openspec-ff-change`

OpenSpec schema `spec-driven` does **not** list `owner-contract.md`. This contract binds
continue/ff to create it **before** any schema artifact (`proposal`, `specs`, `design`,
`tasks`, `decisions`).

## Gate

After resolving `changeRoot` from `openspec status --change "<name>" --json`:

| Condition | Action |
|-----------|--------|
| `<changeRoot>/owner-contract.md` **missing** | Create **only** `owner-contract.md` this invocation; do **not** write schema artifacts in the same turn (continue) or before contract exists (ff). |
| **Present** | Proceed with schema artifacts; read contract as semantic upstream. |

Fail-closed: do not invent Owner decisions in `## Решения Owner` without durable Owner
provenance (chat answers in this cycle, evolution, intent receipt, or explicit Owner text).

## Write path

```text
<changeRoot>/owner-contract.md
```

## Required sections (Russian product headings)

Use this skeleton. Fill from bounded sources; leave `TBD` only where Owner attention
records an open item.

```markdown
# Owner Contract

## Что хотим получить

## Зачем

## Что должно остаться неизменным

## Чего сейчас не делаем

## Как поймём, что получилось

## Решения Owner

(bullets — only proven Owner semantics)

## Owner attention

OWNER_ATTENTION_STATUS: OPEN | CLOSED_ON_REVIEW

Product forks and confirmations that MUST surface to Owner before planning treats
semantics as settled. List each as OA-00N with one line question or decision gap.
Do not hide these only in proposal/design.

- OA-001: …

## Статус

Draft created by /opsx-continue | /opsx-ff. NEXT: /opsx-owner-contract-review change=<name>
```

## Sources (priority)

1. Owner wording and answers in the **current** chat cycle
2. Linked `openspec/evolution/**` when STAR/INC referenced in change context
3. Durable `opsx/intents/<hash>.md` when bound to this change
4. `OWNER_REVIEW_HANDOFF` repairs (writer hop only — not continue/ff)
5. Repository facts — **for CONTRACT_VS_REALITY notes only**, not new product scope

Forbidden: copying full proposal/spec text into the contract as a substitute for Owner
sections.

## After write (chat output)

Emit:

```text
ARTIFACT: owner-contract.md
NEXT_ACTION: /opsx-owner-contract-review change=<name>
OWNER_ATTENTION: review ## Owner attention items
```

Soft gate: OCR PASS is recommended before heavy planning; continue/ff do not block on OCR
in v1, but MUST NOT skip creating the contract file.
