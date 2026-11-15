---
component_id: opsx.commands.projectize
component_version: 1.0.0-greenfield
runtime_class: ACTIVE
ownership: TARGET_ONLY
contract_id: OPSX-STROY-PROJECTIZE-01
canonical_source_body: none
required_adapters: ["opsx-lite"]
local_override_policy: TARGET_OVERRIDE_HIGHEST_PRIORITY
---

# /opsx-projectize

Projectize OPSX for `stroy-system`.

## Canonical inputs

Read in this order:
1. `.project/PROJECT_INTENT.md` — Owner intent.
2. `.project/PROJECT_PROFILE.yaml` — machine-readable project profile.
3. `.project/AI_PROJECT_CONTEXT.md` — bounded AI context.
4. Actual repository facts.
5. Existing `opsx/**` bootstrap state.

`.project/**` is project truth. `opsx/**` may adapt to it but must not silently rewrite it.

## Greenfield dynamic projectization

This repository is greenfield and its product stack may remain intentionally unresolved.

Projectization MAY be `COMPLETE` with `normal_commands_enabled: true` when the `project-generic` adapter is installed and machine-checked capability gates are active.

In that state:

- unresolved product capabilities remain explicit in `opsx/project/capabilities.yaml`;
- they are not guessed from the donor;
- only commands requiring an unresolved capability fail closed with `TARGET_CAPABILITY_UNRESOLVED`;
- generic OPSX capabilities and isolated control-plane runtime remain available.

`COMPLETE` means the OPSX realization is coherent for the current project state. It does not mean the product architecture is fully decided.

Do not infer Python, Node, Poetry, npm, framework, product roots, test roots, database, queue, or deployment platform from the OPSX donor.

## READ_ONLY mode

1. Read canonical `.project/**`.
2. Inspect actual repo facts.
3. Separate FACT, OWNER_INPUT, INFERENCE, PROPOSAL and DECISION.
4. Determine unresolved fields that block a safe local OPSX realization.
5. Propose the smallest adapter/capability set required for the current product slice.
6. No writes.

## GO mode

Exact `/opsx-projectize:GO` authorizes writes only to OPSX/process realization surfaces.
It must not write product implementation merely to make OPSX fit.

Set COMPLETE only when required project facts are resolved, selected adapters match actual facts, installed command contracts contain no foreign project assumptions, activation identity matches command bytes, and projectization checks pass.

## Hard invariants

- Never copy donor project realization as project truth.
- Never silently convert an unresolved project field into a decision.
- Never enable normal mutation commands before projectization COMPLETE.
- Never overwrite `.project/**` as a projectization side effect.
- Never install dependencies, commit, push, PR, or deploy.
- Preserve existing product files if/when they appear.

## Current Owner constraints

- Initial product: Telegram + Avito + MAX lead funnel -> AmoCRM.
- P0: accepted leads must not be silently lost.
- P0: fast initial delivery.
- P1: high throughput / low unnecessary latency.
- Redis is an intended technology candidate, not automatically the durable source of truth.
- AI and Temporal are future/deferred capabilities, not initial requirements.

## Output contract

STATUS: PROJECTIZATION_PLAN | COMPLETE | FAIL_CLOSED | OWNER_DECISION_REQUIRED
PROJECT_PROFILE:
FACTS:
UNRESOLVED:
PROPOSED_ADAPTERS:
PROPOSED_CAPABILITIES:
FOREIGN_ASSUMPTIONS_FOUND:
OWNER_DECISION_REQUIRED:
NEXT_ACTION:
