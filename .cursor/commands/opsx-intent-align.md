---
component_id: opsx.commands.intent_align
component_version: 0.1.0
ownership: TARGET_ONLY
runtime_class: ACTIVE_WITH_LIMITS
canonical_source_body: adapted_target_native
required_adapters: ["opsx-lite", "project-generic"]
local_modification_note: "OPSX_FULL_COMMAND_TRANSFER from pinned game source; target-native standalone alignment/staging capability; downstream /opsx-new and /opsx-ff consumers intentionally not modified"
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
contract_id: OPSX-WB-INTENT-ALIGN-01
newline_normalization: none
source_commit: 91311ada29721cfb9aab7a00e1dc4d2a0a8d1982
description: "Restate Owner product intent, resolve material ambiguity, require explicit hash-bound confirmation, and create a durable staging receipt"
argument-hint: "request=\"<text>\" | change=<id> | confirm intent_hash=<sha256> [paste PRIOR_ALIGNMENT_CHALLENGE]"
---


# TARGET OVERRIDE — HIGHEST PRIORITY

**Contract:** `OPSX-WB-INTENT-ALIGN-01`
**Runtime class:** `ACTIVE_WITH_LIMITS`
**Limit:** alignment and staging are active; downstream receipt consumption is
not wired into protected target planning writers.

# PROJECT TARGET PROFILE — REQUIRED

Product-specific execution authority resolves only through:

  opsx/project/capabilities.yaml

Adapter-specific supplements apply only when that adapter is selected in
capabilities.yaml (see opsx/adapters/<adapter-id>/).

Donor literals in this command are historical compatibility text, not authority.
Before product-specific reads, writes, or commands, resolve the required capability.
If unresolved, fail closed with TARGET_CAPABILITY_UNRESOLVED.

Control-plane paths under tools/temporal and tools/cursor_executor are separate
from product runtime adoption.


# /opsx-intent-align

## Target authority and write boundary

Read:

- `$ARGUMENTS` and Owner answers in the current alignment cycle;
- pasted `EVOLUTION_HANDOFF` / `EXPLORE_HANDOFF` as constraints, never as a
  replacement for the Owner's requested WHAT;
- `opsx/projectization.yaml`;
- `opsx/adapters/poetry-python/README.md`;
- existing `opsx/intents/<INTENT_HASH>.md` only when confirming the same hash.

May write:

```text
opsx/intents/<INTENT_HASH>.md
```

The only writer is the bounded helper:

```text
poetry run python .cursor\pipeline\intent-align\intent_alignment.py
```

Must not write:

- `owner-contract.md`, proposal, design, specs, tasks, decisions, or product
  code;
- `<changeRoot>/opsx/intent-alignment.md`;
- `.cursor/pipeline/GRAPH.md` or `.cursor/pipeline/META.md`;
- `/opsx-new`, `/opsx-ff`, or `/opsx-owner-contract-review`;
- any receipt before explicit Owner confirmation.

Do not install dependencies, access production/Kubernetes/secrets, or commit,
push, merge, rebase, checkout, reset, clean, stash, or deploy.

## Projectization gate

Read `opsx/projectization.yaml` before every run. If `status` is not
`COMPLETE` or `normal_commands_enabled` is not `true`, stop without writes:

```text
INTENT_ALIGNMENT
INTENT_STATUS: BLOCKED
REASON: OPSX_PROJECTIZATION_REQUIRED
FILES_CHANGED: none
NEXT_ACTION: /opsx-projectize
```

## Purpose and authority

`/opsx-intent-align` is the sole writer of target staging intent receipts. It
aligns only product semantics: what the Owner wants, why, Owner-visible
behavior, important invariants, and scope boundaries.

It is not a technical-design authority. Never select storage, API shape,
schema, component placement, caching, debounce, framework, or implementation
strategy. `TECHNICAL_DECISIONS_MADE` must remain `NONE`.

It is not an Owner Contract writer and does not claim that `/opsx-new` or
`/opsx-ff` validates, transfers, invalidates, or consumes the receipt.

## Input

```text
$ARGUMENTS
```

Accepted forms:

- a free-form Owner request or `request="<text>"`;
- optional `change=<CHANGE_ID>` as a non-authoritative binding;
- Owner answers to product-semantic clarification questions;
- `confirm`, `yes`, `да`, `подтверждаю`, `согласовано`, or
  `restatement confirmed`, together with the current hash-bound
  `PRIOR_ALIGNMENT_CHALLENGE`;
- an explicitly pasted previously emitted challenge and restatement.

Bare `GO`, `OK`, and `proceed` are process acknowledgements, not intent
confirmation. Reject them with `AMBIGUOUS_CONFIRMATION_TOKEN`.

## Exclusive state model

Emit exactly one `INTENT_STATUS` per response and exactly one
`INTENT_STATUS` in every receipt.

| `INTENT_STATUS` | Meaning | Durable write |
|---|---|---|
| `OWNER_CLARIFICATION_REQUIRED` | A material product ambiguity remains. | none |
| `OWNER_CONFIRMATION_REQUIRED` | The restatement is visible and clear, but not yet explicitly confirmed. | none |
| `ALIGNED` | The Owner explicitly confirmed this exact restatement hash and the staging receipt exists. | one receipt |
| `BLOCKED` | Input, confirmation, identity, or existing state is invalid/stale. | none |

Never combine states, including `ALIGNED` with `BLOCKED` or `TRANSFERRED`.
`TRANSFERRED` may appear only in a future consumer-owned rewrite of staging;
this command cannot create or revive it.

## Mandatory material-ambiguity gate

Before showing a restatement:

1. Derive the most probable product interpretation.
2. Derive one plausible alternative interpretation.
3. Ask whether the two produce materially different Owner-visible behavior,
   workflow, durability expectation, business rule, invariant, or scope.
4. If yes, set `MATERIAL_AMBIGUITY: true`,
   `INTENT_STATUS: OWNER_CLARIFICATION_REQUIRED`, and ask only the minimum
   product question needed to select the meaning.
5. If no, dismiss the alternative and ask zero clarification questions.

Questions that only affect technical implementation are forbidden. Ask at
most 3–5 independent product questions; more than five is
`QUESTIONNAIRE_SPAM_VIOLATION`.

## Restatement challenge

For a clear request, produce these visible sections in plain Owner language:

- `SYSTEM_RESTATEMENT`;
- `OWNER_PROBLEM_AND_WHY`;
- `OWNER_VISIBLE_BEHAVIOR`;
- `INVARIANTS_AND_SCOPE_BOUNDARIES`.

Then use the helper's `prepare` action with the exact raw request and exact
visible `SYSTEM_RESTATEMENT`. `prepare` is read-only and returns:

```text
INTENT_STATUS: OWNER_CONFIRMATION_REQUIRED
RAW_REQUEST_SHA256: <sha256>
SYSTEM_RESTATEMENT_SHA256: <sha256>
INTENT_HASH: <sha256(raw-hash + ":" + restatement-hash)>
FILES_CHANGED: none
```

Emit the following challenge so a later confirmation is bound to the text the
Owner actually saw:

```text
PRIOR_ALIGNMENT_CHALLENGE
INTENT_STATUS: OWNER_CONFIRMATION_REQUIRED
INTENT_HASH: <hash>
RAW_REQUEST_SHA256: <hash>
SYSTEM_RESTATEMENT_SHA256: <hash>
SYSTEM_RESTATEMENT:
<exact visible restatement>
```

Do not create a file in this state.

## Confirmation gate

An `ALIGNED` transition requires all of the following:

1. The Owner has already seen the exact restatement. A confirmation in the
   same turn as a newly generated, previously unseen restatement is invalid.
2. The Owner supplied one accepted explicit confirmation token. Model
   confidence, silence, inferred assent, or the agent's own text is invalid.
3. The invocation has the prior challenge from an earlier response, or the
   Owner explicitly pasted a previously emitted challenge and restatement.
4. The helper recomputes raw-request, restatement, and intent hashes; both
   `expected-intent-hash` and `prior-challenge-hash` match the current
   `INTENT_HASH`.
5. `MATERIAL_AMBIGUITIES: []`,
   `UNRESOLVED_HIGH_IMPACT_ASSUMPTIONS: []`, and
   `TECHNICAL_DECISIONS_MADE: NONE`.

If the Owner changes or corrects the meaning while confirming, generate a new
restatement and return to `OWNER_CONFIRMATION_REQUIRED`; never confirm the
unseen revision in that turn.

On valid confirmation, invoke the helper's `confirm` action with exact
semantic fields, expected hash, prior challenge hash, explicit token, and
exact confirmation evidence. The helper:

- rejects stale/malformed/path-like hashes;
- rejects bare process acknowledgements;
- creates only `opsx/intents/<INTENT_HASH>.md`;
- uses an atomic no-overwrite publication;
- treats an identical existing `ALIGNED` receipt as idempotent;
- rejects conflicting, malformed, or `TRANSFERRED` existing state.

Only after the helper reports `INTENT_STATUS: ALIGNED` and the exact receipt
exists may this command emit `ALIGNED`.

## Durable receipt

The receipt must contain:

```text
COMMAND: /opsx-intent-align
CHANGE_ID: <id or unassigned>
INTENT_STATUS: ALIGNED
INTENT_HASH: <sha256>
RAW_REQUEST_SHA256: <sha256>
SYSTEM_RESTATEMENT_SHA256: <sha256>
ALIGNMENT_PAYLOAD_SHA256: <sha256>
SYSTEM_RESTATEMENT_CONFIRMED: YES
OWNER_CONFIRMATION: YES
TECHNICAL_DECISIONS_MADE: NONE
MATERIAL_AMBIGUITIES: []
UNRESOLVED_HIGH_IMPACT_ASSUMPTIONS: []
```

It also preserves the confirmed restatement, Owner problem, Owner-visible
behavior, invariants/scope, exact raw request, and explicit confirmation
evidence.

## Integration boundary and honest handoff

Current target `/opsx-new` and `/opsx-ff` do not have verified
`intent_hash=` receipt consumption or transfer logic. Therefore every
`ALIGNED` response must say:

```text
INTENT_CONSUMER_INTEGRATION: MISSING
DOWNSTREAM_RECEIPT_ENFORCEMENT: NOT_WIRED
CANONICAL_TRANSFER: NOT_PERFORMED
```

The capability remains useful: emit a self-contained `INTENT_HANDOFF` with the
confirmed `OWNER_INTENT`, hashes, and `STAGING_FILE`. The Owner can supply that
handoff as semantic context to a planning writer, but this command must not
claim consumer parity, automatic freeze enforcement, transfer to a change, or
staging invalidation.

## Output contracts

### Clarification

```text
INTENT_ALIGNMENT
INTENT_STATUS: OWNER_CLARIFICATION_REQUIRED
SYSTEM_RESTATEMENT_CONFIRMED: NO
OWNER_CONFIRMATION: NO
TECHNICAL_DECISIONS_MADE: NONE
MATERIAL_AMBIGUITY: true
OWNER_QUESTION: <minimal product-semantic question>
MODE: ACTIVE
FILES_CHANGED: none
NEXT_ACTION: answer OWNER_QUESTION in this alignment cycle
```

### Confirmation required

Show the four semantic sections and `PRIOR_ALIGNMENT_CHALLENGE`, then:

```text
INTENT_ALIGNMENT
INTENT_STATUS: OWNER_CONFIRMATION_REQUIRED
INTENT_HASH: <hash>
RAW_REQUEST_SHA256: <hash>
SYSTEM_RESTATEMENT_SHA256: <hash>
SYSTEM_RESTATEMENT_CONFIRMED: NO
OWNER_CONFIRMATION: NO
TECHNICAL_DECISIONS_MADE: NONE
MATERIAL_AMBIGUITIES: []
MODE: ACTIVE
FILES_CHANGED: none
NEXT_ACTION: reply confirm (or another accepted explicit token) with this challenge in context
```

### Aligned

```text
INTENT_ALIGNMENT
INTENT_STATUS: ALIGNED
INTENT_HASH: <hash>
RAW_REQUEST_SHA256: <hash>
SYSTEM_RESTATEMENT_SHA256: <hash>
SYSTEM_RESTATEMENT_CONFIRMED: YES
OWNER_CONFIRMATION: YES
TECHNICAL_DECISIONS_MADE: NONE
MATERIAL_AMBIGUITIES: []
INTENT_CONSUMER_INTEGRATION: MISSING
DOWNSTREAM_RECEIPT_ENFORCEMENT: NOT_WIRED
CANONICAL_TRANSFER: NOT_PERFORMED

INTENT_HANDOFF
OWNER_INTENT:
<confirmed restatement>
INTENT_HASH: <hash>
RAW_REQUEST_SHA256: <hash>
SYSTEM_RESTATEMENT_SHA256: <hash>
STAGING_FILE: opsx/intents/<INTENT_HASH>.md
CONSUMER_VERIFICATION: NOT_WIRED

MODE: ACTIVE_WITH_LIMITS
FILES_CHANGED: opsx/intents/<INTENT_HASH>.md
NEXT_ACTION: supply this handoff as context to /opsx-new; do not claim automatic receipt consumption
```

### Blocked

```text
INTENT_ALIGNMENT
INTENT_STATUS: BLOCKED
REASON: AMBIGUOUS_CONFIRMATION_TOKEN | OWNER_CONFIRMATION_REQUIRED |
        INTENT_ALIGNMENT_STALE | TECHNICAL_DECISION_FORBIDDEN |
        QUESTIONNAIRE_SPAM_VIOLATION | INVALID_EXISTING_RECEIPT
SYSTEM_RESTATEMENT_CONFIRMED: NO
OWNER_CONFIRMATION: NO
TECHNICAL_DECISIONS_MADE: NONE
MODE: ACTIVE_WITH_LIMITS
FILES_CHANGED: none
NEXT_ACTION: <one concrete correction>
```

## Self-check

- [ ] Projectization is COMPLETE and normal commands are enabled.
- [ ] Exactly one status is emitted.
- [ ] The material-ambiguity test ran.
- [ ] No technical decision or technical Owner question was introduced.
- [ ] The exact restatement was visible before confirmation.
- [ ] Confirmation is explicit and bound to the current prior challenge hash.
- [ ] Bare `GO` / `OK` / `proceed` and model self-confirmation are rejected.
- [ ] No receipt exists before confirmation.
- [ ] `ALIGNED` implies the durable staging receipt exists and validates.
- [ ] No Owner Contract, planning artifact, product code, graph, or canonical
      change receipt was written.
- [ ] Missing downstream consumer integration is reported, not inferred away.

Static/helper tests:

```text
.cursor/pipeline/intent-align/intent_alignment.py
.cursor/pipeline/intent-align-tests/test_intent_alignment.py
```
