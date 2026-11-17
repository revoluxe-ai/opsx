---
description: "Converge one IMPLEMENTATION_WRITTEN slice to GREEN_PROVEN by running frozen RED, diagnosing failures, and applying bounded production-only fixes. Does not modify tests or claim whole-change verification."
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


# /opsx-apply-green

For **one slice per invoke**, the only APPLY stage that may **run tests → diagnose → fix production → rerun**. Prove this slice satisfies frozen RED (plus bounded adjacent checks), without editing tests or claiming archive/whole-change verify.

## Invocation

```text
/opsx-apply-green change=<change-id>
```

Optional `slice=<SLICE-ID>` when eligible. Resolve `changeRoot` via `openspec status --change "<id>" --json`.

## Read

- `<changeRoot>/apply-design/APPLY-DESIGN.json`
- `<changeRoot>/apply-scaffold/<SLICE-ID>/SCAFFOLD.json`
- `<changeRoot>/apply-red/<SLICE-ID>/RED.json`
- `<changeRoot>/apply-implement/<SLICE-ID>/IMPLEMENTATION.json`

Predecessor pair for the slice:

- **Normal:** RED proven + `IMPLEMENTATION_WRITTEN`
- **Adoption:** RED already-satisfied observation + `IMPLEMENTATION_ADOPTED` (implement wrote no product/tests)

Selection: `selectNextGreenableSlice` in `opsx_tools/opsx-apply-green.mjs`. Treat stale old GREEN receipts as needing re-run when upstream artifact bytes changed. Deps need **current** GREEN, not status-only.

Runner: `runFrozenRedCommand` / `runBoundedGreenCheck` in `opsx_tools/opsx-apply-green.mjs`.

## Write

```text
<changeRoot>/apply-green/<SLICE-ID>/GREEN.md
<changeRoot>/apply-green/<SLICE-ID>/GREEN.json
bounded production fixes within slice scope
```

When all slices are GREEN, may write `<changeRoot>/apply-complete/TASK-COMPLETION.json` (derived task completion projection — do not mutate `tasks.md` checkboxes).

Schema: `opsx_tools/registry/apply_green.schema.json`  
Validate: `node opsx_tools/opsx-apply-green.mjs <GREEN.json>`

Production fixes: `expected_product_write_surfaces` + frozen `architectural_intent` for **this slice only** — same semantic scope rule as implement (path ≠ later-slice behavior).

## Work

1. **Frozen RED:** run exact `test_commands` from RED. Re-check test file bytes before/after every production edit; mismatch → stop (do not repair tests).
2. **If RED fails:** classify implementation-owned failures (behavior missing, bug, integration mismatch, slice regression) vs infra/design/planning/test drift — only the first group enters the fix loop (default ≤5 iterations; stop on no progress).
3. **After RED passes:** bounded focused/adjacent checks from slice `completion_contract`, `relevant_existing_surfaces`, composition obligations with `B_GREEN_ADJACENT`, and directly adjacent module tests — not whole suite, not `/opsx-verify`, not browser matrix. Tests named only for **future** slices' RED seams are not required here. Re-run frozen RED after any production fix before trusting adjacent pass.
4. **Success:** frozen tests unchanged, RED commands pass, required adjacent checks pass or genuinely N/A, `whole_change_verified: false`.

## Must not

- Modify RED tests, fixtures, or expectations to match code
- Weaken assertions, rewrite APPLY DESIGN/specs/tasks
- Run full project suite, Bugbot hunt, or change-level verify
- Install deps, deploy, push, PR

## Stop when

Stale upstream receipts, frozen tests mutated, dependency not GREEN, environment/infra blocking required adjacent execution, design/planning must change, scope expansion, iteration limit — state blocker. GREEN for one slice is not release-ready or archive PASS.
