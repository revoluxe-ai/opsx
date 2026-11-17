---
description: "Materialize the production SHAPE required so RED can address a frozen seam. Writes product shape + apply-scaffold receipts. Does not implement behavior, author tests, or run tests."
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


# /opsx-apply-scaffold

For **one slice per invoke**, create the minimum production **SHAPE** so RED fails on missing **behavior**, not missing files/exports/symbols.

## Invocation

```text
/opsx-apply-scaffold change=<change-id>
```

Optional `slice=<SLICE-ID>` only when that slice is eligible; otherwise use deterministic selection.

Resolve `changeRoot` with `openspec status --change "<id>" --json`.

## Read

- `<changeRoot>/apply-design/APPLY-DESIGN.json` (do not rewrite)
- `<changeRoot>/apply-design-review/APPLY-DESIGN-REVIEW.json` — design-review must have passed before scaffold
- Prior slice `<changeRoot>/apply-green/<DEP>/GREEN.json` when dependencies exist (read-only)

Selection helper: `selectNextScaffoldableSlice` in `opsx_tools/opsx-apply-pipeline-identity.mjs`.

## Write

**Receipts:**

```text
<changeRoot>/apply-scaffold/<SLICE-ID>/SCAFFOLD.md
<changeRoot>/apply-scaffold/<SLICE-ID>/SCAFFOLD.json
```

**Product:** only paths in the slice `scaffold_contract` (`required_surfaces`, `allowed_scaffold_writes`, `expected_product_write_surfaces` for shape). Wider paths → stop (scope expansion).

Schema: `opsx_tools/registry/apply_scaffold.schema.json`  
Validate: `node opsx_tools/opsx-apply-scaffold.mjs <SCAFFOLD.json>`

## One slice per invoke

Pick the first eligible slice in `slice_order.nodes` order. Eligible when: no `SCAFFOLD_READY` receipt yet for that slice; every `dependencies_on_prior_slices` id has proven GREEN for the change. Report other eligible ids; do not scaffold them in this run.

If required surfaces already exist → valid **no-op** (`scaffold_changes: []`, `no_op: true`).

## Allowed SHAPE work

MAY: create planned files, exports, class/function signatures, adapter/interface skeletons, DI seams, minimal registration to expose a frozen seam.

MUST NOT implement business behavior (dedupe, retry, auth, persistence, orchestration, debounce, error recovery, branching semantics, user-visible behavior).

Placeholders may use explicit `OPSX_SCAFFOLD_NOT_IMPLEMENTED` so RED can distinguish sentinel from broken imports/fixtures.

## Must not

- Author, modify, or run tests
- Modify APPLY DESIGN, RED/IMPLEMENT/GREEN receipts, specs, tasks
- Implement sibling stages (RED, implement, GREEN)
- Install deps, deploy, push, PR

## Stop when

Design or review authority missing/stale, slice graph invalid, dependency not GREEN, scaffold contract disagrees with repo in a way that changes architecture — state the blocker; do not expand scope or implement behavior to unblock.
