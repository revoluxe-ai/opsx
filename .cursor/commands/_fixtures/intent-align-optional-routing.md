# Fixture: optional `/opsx-intent-align` routing (evolution + explore)

Process-only contract for exposing intent-align as an **optional** staging
step without making it mandatory or auto-dispatched.

Commands under test: `/opsx-evolution`, `/opsx-explore` (routing only).
Authority command (unchanged): `/opsx-intent-align`.

## Acceptance criteria (static)

| # | Criterion | evolution | explore |
|---|-----------|-----------|---------|
| 1 | Optional `/opsx-intent-align` route documented (human purpose) after handoff | yes | yes (Recommended Workflow / Next Action) |
| 2 | Default remains `/opsx-new` when `OWNER_INTENT` clear | yes | yes |
| 3 | Intent-align never mandatory from evolution/explore | yes | yes |
| 4 | No `/opsx-*` inside evolution `COPY_BLOCK` fence | yes | n/a |
| 5 | No auto-dispatch of intent-align | yes | yes |
| 6 | Explore recommends on Owner request OR material **product-semantic** ambiguity (not technical design) | n/a | yes |
| 7 | Explore preserves `OWNER_INTENT` verbatim; no intent receipts | n/a | yes |
| 8 | Boundaries: sole receipt writer is intent-align; evolution/explore cannot self-confirm `ALIGNED`; `NOT_WIRED` consumer; `INTENT_HANDOFF` vs `EVOLUTION_HANDOFF` contradiction → Owner correction | yes | yes |

## E2E (manual / chat)

```text
Given evolution emitted COPY_BLOCK with clear OWNER_INTENT
When Owner copies to new chat
Then default guidance is /opsx-new (or ff/update per collision)
And optional note may mention /opsx-intent-align without requiring it

Given explore finds two product readings of the same Owner phrase
When Owner has not confirmed which behavior they want
Then explore recommends /opsx-intent-align (optional)
And does not write opsx/intents/*
```
