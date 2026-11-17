---
description: "Write production implementation for the next eligible RED_PROVEN APPLY DESIGN slice. Writes product code + apply-implement receipts. Does not run or modify tests."
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


# /opsx-apply-implement

For **one slice per invoke**, write production behavior for a slice with frozen scaffold + RED (or follow the adoption path when RED recorded already-satisfied — no product writes on that branch until audit/authority rules in the implement tool apply). **No test execution.**

## Invocation

```text
/opsx-apply-implement change=<change-id>
```

Optional `slice=<SLICE-ID>` when eligible. Resolve `changeRoot` via `openspec status --change "<id>" --json`.

## Read

- `<changeRoot>/apply-design/APPLY-DESIGN.json`
- `<changeRoot>/apply-scaffold/<SLICE-ID>/SCAFFOLD.json`
- `<changeRoot>/apply-red/<SLICE-ID>/RED.json` (`RED_PROVEN` or adoption observation per tool)
- `<changeRoot>/apply-green/<DEP>/GREEN.json` for dependencies
- `.cursor/rules/testing-rules.mdc` (frozen RED is read-only input)

Selection: `selectNextImplementableSlice` in `opsx_tools/opsx-apply-implement.mjs`.

## Write

```text
src/**, backend/app/**, other production trees named by the slice
<changeRoot>/apply-implement/<SLICE-ID>/IMPLEMENTATION.md
<changeRoot>/apply-implement/<SLICE-ID>/IMPLEMENTATION.json
```

Schema: `opsx_tools/registry/apply_implement.schema.json`  
Validate: `node opsx_tools/opsx-apply-implement.mjs <IMPLEMENTATION.json>`

Stay within `expected_product_write_surfaces`. Closely coupled new files only when frozen `architectural_intent` requires — record in receipt; broad expansion → stop.

### Semantic slice scope

Authorized **paths** are not license for **later slices' behavior**. Implement only this slice's `responsibility`, `source_task_ids`, `required_behavior`, `architectural_intent`, `invariants`, `completion_contract`. Future-slice semantics on an authorized path is still out of scope.

May keep scaffold slots reserved for later slices; must not fill them with later-slice behavior.

Frozen `reuse_decision` with `kind=reuse` → import/extend the named surface; do not silently clone a second mechanism.

## Process

1. Read frozen design slice, scaffold, RED (do not edit).
2. Replace placeholders / `OPSX_SCAFFOLD_NOT_IMPLEMENTED` with real production behavior within slice scope.
3. Bounded inspect of `expected_product_write_surfaces` and `relevant_existing_surfaces` only.
4. One bounded production-write episode; honor `must_not_change` and `reuse_decisions`.
5. Re-hash frozen test paths (read-only). If test bytes changed during implement → stop (`FROZEN_RED_MUTATED`).

## Must not

- Run any tests or subprocess test runners (including lint/build as validation)
- Modify frozen RED tests/fixtures or RED receipt
- Mark GREEN, verify change, or delegate to combined apply if that would run tests
- Install deps, deploy, push, PR

## Stop when

Stale design/RED/scaffold, dependency not GREEN, repo contradicts frozen architecture, implementation would require planning/spec change, scope expansion, frozen tests mutated — state conflict (`planning_claim` / `repository_fact` / paths). Do not silently redesign.

Implement means code written for the slice — not GREEN, not whole-change complete.
