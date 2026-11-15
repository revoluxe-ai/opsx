# Fixture 01 — valid frozen change + repository reality

## Given

`change=fixture-hydrate`

Frozen planning exists at status-resolved `changeRoot`:

- `tasks.md` with checkbox tasks T1, T2, T3, T4
- `design.md` + `specs/hydrate-coord/spec.md` (WHAT package)
- owner-contract optional/absent (not required for this fixture)

Repository reality:

- `src/services/hydrateCoordinator.js` already coordinates cloud hydrate
- `src/store/progress.js` is the existing client progress mirror
- No second hydrate mechanism exists

Tasks:

- T1 and T2 implement the same hydrate-coordination invariant (group)
- T3 is an independent UI progress-branch responsibility (split)
- T4 wires resume triggers and depends on T1/T2 slice and T3 slice

Planning does not contradict ownership or SoT.

Chat history is empty / a different chat than any prior research.

## Expected

```text
STATUS: APPLY_DESIGN_READY
CHANGE: fixture-hydrate
DESIGN_ARTIFACT: openspec/changes/fixture-hydrate/apply-design/APPLY-DESIGN.json
SLICES: SLICE-001, SLICE-002, SLICE-003
ORDER: SLICE-001 -> SLICE-003 ; SLICE-002 -> SLICE-003 (SLICE-001 and SLICE-002 independent)
CROSS_SLICE_CONTRACTS: CSC-003 covers both edges (upstream SLICE-001+SLICE-002 → SLICE-003)
COMPOSITION_COMPLETENESS: task_coverage T1–T4; race+lifecycle applicable; dogfood failure classes classified
BLOCKERS: none
NEXT_STAGE: APPLY_DESIGN_REVIEW
NEXT_ACTION: /opsx-apply-design-review change=fixture-hydrate
CHAT_HISTORY_REQUIRED: false
WRITE_BOUNDARY.product_code: false
WRITE_BOUNDARY.tests: false
REUSE: explicit reuse of hydrateCoordinator (do not introduce a second coordinator)
```

Golden machine artifact: `01-ready-valid-change.APPLY-DESIGN.json`

SLICE-001 `source_task_ids` includes T1 and T2 (grouped).
SLICE-002 is T3 only (split independent responsibility).
`slice_order.independent_groups` includes `[SLICE-001, SLICE-002]`.
`reuse_decisions` is non-empty.
`schema_version` is `3`.
`cross_slice_contracts` includes CSC-003 with `proof_placement` mixing `B_RED` and `B_GREEN_ADJACENT`,
`green_adjacent_seams` naming `tests/unit/crossDeviceVisibilityResumeHydrate.test.mjs`, and
`failure_classes` including `IN_FLIGHT_JOIN_BY_BOOTSTRAP` and `CONTROL_RESULT_AS_FAILURE`.
No `chat_history` field is required to consume the artifact.
