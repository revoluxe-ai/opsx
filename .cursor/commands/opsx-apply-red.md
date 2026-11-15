---
description: "Author and prove RED tests for the next eligible APPLY DESIGN slice. Writes tests + apply-red receipts. Does not edit production code."
argument-hint: "change=<change-id>"
---

# /opsx-apply-red

For **one slice per invoke**: author planned tests, run bounded commands, prove behavior is **missing for the intended reason** (or record already-satisfied observation). Does not touch production or scaffold.

## Invocation

```text
/opsx-apply-red change=<change-id>
```

Optional `slice=<SLICE-ID>` when eligible. Resolve `changeRoot` via `openspec status --change "<id>" --json`.

Coverage reconcile (post-GREEN missing planned artifact only):

```text
/opsx-apply-red change=<id> mode=coverage-reconcile slice=<SLICE-ID>
```

Writes missing test + `COVERAGE-RECONCILE.json/md` under `apply-red/<SLICE-ID>/`; do not rewrite historical RED/GREEN receipts or fabricate RED history.

## Read

- `<changeRoot>/apply-design/APPLY-DESIGN.json`
- `<changeRoot>/apply-scaffold/<SLICE-ID>/SCAFFOLD.json` (`SCAFFOLD_READY`)
- `<changeRoot>/apply-green/<DEP>/GREEN.json` for dependencies
- `.cursor/rules/testing-rules.mdc` (causal seam; do not fully mock the seam under test)

Selection: `selectNextEligibleSlice` in `opsx_tools/opsx-apply-red.mjs`.

## Write

```text
tests/unit/**/*.test.mjs
backend/tests/unit/test_*.py
fixtures under those trees
<changeRoot>/apply-red/<SLICE-ID>/RED.md
<changeRoot>/apply-red/<SLICE-ID>/RED.json
```

Schema: `opsx_tools/registry/apply_red.schema.json`  
Validate: `node opsx_tools/opsx-apply-red.mjs <RED.json>`

## One slice per invoke

Eligible: `SCAFFOLD_READY`; not already durable RED complete for slice; deps have GREEN. First eligible id in `slice_order.nodes`. No RED before scaffold; no RED for downstream slice while upstream deps lack GREEN.

## RED work

1. Read frozen slice + `cross_slice_contracts` where this slice is dependent; materialize `proof_placement: B_RED` obligations.
2. Reuse/extend seams in `red_test_targets_or_seams`.
3. Author **all** mapped test obligations (design targets, scenario `required_test_artifacts` owned by this slice, B_RED composition cases) plus minimum cases for `required_behavior` / `invariants`. Missing mapped artifact → stop (not RED complete).
4. Run **only** bounded per-test commands (adapter: frontend `node --test` with optional `--test-name-pattern`; backend `poetry run python -m unittest …` from `backend/`). Resolve ids via `opsx_tools/opsx-run-tests-resolve.mjs`. Never whole-suite glob as proof.
5. Qualify **each** command independently. Valid RED: reaches intended seam and fails on behavioral assertion **or** hits `OPSX_SCAFFOLD_NOT_IMPLEMENTED` at the frozen scaffold seam (`INTENDED_BEHAVIOR_MISSING`). Wrong reason (syntax, fixture, missing import/symbol after scaffold, infra, unrelated) → repair **test/fixture only**, rerun (default ≤3 attempts per command). Scaffold defects → stop; do not fix scaffold from RED.

If required tests **pass** before implementation: do not manufacture failure. When all mapped commands pass with complete evidence → durable **already-satisfied observation** (not behavioral RED proof); implement stage handles adoption — observation alone does not authorize product writes.

Freeze in `RED.json`: `test_paths`, `fixture_paths`, `test_commands`, per-command execution evidence, identities linking design/scaffold. `tests_frozen: true` only when every command is RED-qualified.

Copy `expected_product_write_surfaces` into human RED.md for implement.

## Must not

- Modify production or scaffold
- Run full suite, browser acceptance, or bug hunts
- Install deps, deploy, push, PR

## Stop when

Stale/missing design or scaffold, deps not ready, unqualified RED after attempt limit, design/reality mismatch for tests, mixed pass/fail on observation path — state blocker precisely.
