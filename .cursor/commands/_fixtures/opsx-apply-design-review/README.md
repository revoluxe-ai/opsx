# Fixtures: /opsx-apply-design-review

Process-only regression cases for APPLY DESIGN REVIEW. Command under test:
`/opsx-apply-design-review`.

These fixtures do **not** authorize product, test, or APPLY DESIGN writes. A
correct executor MUST emit the `Expected` status and MUST NOT modify
`src/**`, `tests/**`, or `apply-design/**`.

Golden machine artifacts (`*.APPLY-DESIGN-REVIEW.json`) MUST validate:

```bash
node opsx_tools/opsx-apply-design-review.mjs <golden.json>
```

PASS golden binds identities from design fixture
`.cursor/commands/_fixtures/opsx-apply-design/01-ready-valid-change.APPLY-DESIGN.json`.

## Index

| ID | File | Theme | Expected STATUS |
|----|------|-------|-----------------|
| 01 | `01-pass.md` | READY design + composition contracts → review PASS | APPLY_DESIGN_REVIEW_PASS |
| 02 | `02-repairs-uncovered-interaction.md` | locally complete slices; uncovered critical interaction | REPAIRS_REQUIRED |
| 03 | `03-stale-after-design-change.md` | DESIGN artifact_sha256 change → DESIGN_REVIEW_STALE | gate fail |
| 04 | `04-write-boundary.md` | review cannot edit DESIGN/product/tests | write boundary |
| 05 | `05-scaffold-gate.md` | missing/stale/REPAIRS blocks SCAFFOLD; PASS unlocks | scaffold gate |

## Static matrix

| Requirement | Fixture |
|-------------|---------|
| PASS → next_stage APPLY_SCAFFOLD | 01 |
| Q12 + composed_behavior_risk answered | 01, 02 |
| REPAIRS → next_stage APPLY_DESIGN + repair_handoff | 02 |
| Blocking finding on Q8/Q12 | 02 |
| Stale after DESIGN bytes change | 03 |
| No DESIGN/product/test writes | 04 |
| SCAFFOLD gate via evaluateApplyDesignReviewGate | 05 |
| chat_history_required false | 01, 02 |
