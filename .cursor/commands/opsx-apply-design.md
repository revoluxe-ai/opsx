---
description: "Implementation-architecture research for an approved OpenSpec change. Writes change-local APPLY DESIGN artifacts. Does not edit product code or tests."
argument-hint: "change=<change-id>"
---

## Mandatory planning Architecture acceptance

**Precondition, before any APPLY-design/review, RED, SCAFFOLD, IMPLEMENT,
GREEN or other stage work:** the selected product Change MUST have current
independently reviewed `architecture.md`, per `.cursor/pipeline/contracts/ARCHITECTURE_STAGE_V1.md`.

From the consumer project root run:
```bash
python opsx/core/tools/openspec/architecture_stage_gate.py check --root . --change <change-id>
```
Require `status: READY` (reviewed architecture bytes + current Owner/proposal/specs/
decisions identity). No valid receipt / stale review / missing Core / unavailable
gate => `ARCHITECTURE_STAGE_BLOCKED`: STOP before writing files, running stage
tools or executing tests. Existing split-APPLY, cached GREEN, Super and
ADOPTION receipts cannot substitute the current Architecture Gate. Do not
create review acceptance within an APPLY command. This does not replace
the existing split-APPLY preconditions or Owner GO.


# /opsx-apply-design

Map approved planning onto the **current repository**: HOW to implement, slice boundaries, production shape contracts for scaffold, and cross-slice composition. This is **not** product/spec planning and **not** implementation.

## Invocation

```text
/opsx-apply-design change=<change-id>
```

Resolve `changeRoot` with `openspec status --change "<id>" --json`. Require an explicit change id; do not pick an unrelated active change.

## Read

**Required planning** (stop and name missing paths if absent):

- `<changeRoot>/tasks.md` (at least one checkbox task)
- At least one of `<changeRoot>/design.md`, `proposal.md`, `specs/**/spec.md`

**Also use when present:** `owner-contract.md`, scenario-coverage plan, change `decisions/**`, cited `openspec/decisions/**`, risk notes, `TEST_COVERAGE_PACKET`.

**Repository:** inspect existing code, reuse candidates, ownership/write paths, integration seams, test attachment points, accepted architecture decisions. Bounded, purpose-driven search — not a whole-repo cache.

**Re-run after design-review repairs:** read `<changeRoot>/apply-design-review/` receipt and fix blocking review findings; do not re-emit the same design unchanged.

## Write (only)

```text
<changeRoot>/apply-design/APPLY-DESIGN.md
<changeRoot>/apply-design/APPLY-DESIGN.json
```

Schema: `opsx_tools/registry/apply_design.schema.json`  
Validate when available: `node opsx_tools/opsx-apply-design.mjs <path>`

Human markdown + machine JSON. On blocked runs, still persist artifacts under `apply-design/` when `changeRoot` is known.

## Must not

- Modify production code (`src/**`, `backend/**`, …) or tests (`tests/**`, `backend/tests/**`, …)
- Author RED tests, run tests, implement behavior, or fix bugs found during research
- Change approved specs/tasks to fit code
- Silently resolve planning vs repository architectural contradiction
- Install deps, touch `pyproject.toml` / `poetry.lock`, deploy, push, PR

## Reconcile planning vs reality

If frozen planning contradicts the repo in a way that changes **architecture, ownership, acceptance, or scope** — stop. State `planning_claim`, `repository_fact`, and paths. Do not redesign the approved change.

Missing code the change is supposed to **add** is normal; that is not a conflict.

Before finishing, a later stage must not still need to decide: ownership model, where state lives, authoritative subsystem, or reuse vs a second parallel mechanism.

## Slices

One slice = one coherent responsibility for later scaffold → RED → implement → GREEN (after design-review).

- Group tasks that share the same invariant; split when responsibilities can be validated independently (not 1 task = 1 slice).

Each slice in JSON must include: `slice_id`, `title`, `responsibility`, `source_task_ids`, `requirement_refs`, `architectural_intent`, `reuse_decisions`, `expected_product_write_surfaces`, `relevant_existing_surfaces`, `red_test_targets_or_seams` (seams only — no test bodies), `required_behavior`, `invariants`, `must_not_change`, `dependencies_on_prior_slices`, `implementation_notes`, `completion_contract`, `scaffold_contract`.

`scaffold_contract`: `required_surfaces[]` (`path`, `action` INTRODUCE|EXTEND|EXISTING, `required_symbols[]`, `structural_contract`, `allowed_scaffold_writes[]`, `behavior_deferred[]`), `no_op_allowed`, `forbidden_behavior[]`. DESIGN specifies shape, not business logic.

`expected_product_write_surfaces` and `red_test_targets_or_seams` are **for later stages** — not permission to write them here.

## Order and composition

`slice_order`: dependency graph (`nodes`, `edges`, `independent_groups`).

For every edge, add `cross_slice_contracts[]` covering consumed semantics, interaction invariants, failure modes, and `proof_placement` (e.g. B_RED, B_GREEN_ADJACENT). Emit `composition_completeness` when edges exist. Missing edge coverage or empty composition behavior → stop.

## Human artifact

`APPLY-DESIGN.md`: change id, architecture summary, reuse, constraints, slices, order graph, composition summary, blockers, pointer to JSON.

## Reuse

Prefer extending existing modules/coordinators over introducing a second mechanism beside an existing one.
