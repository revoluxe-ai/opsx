# Fixtures: /opsx-apply-implement

Process-only regression cases. Command: `/opsx-apply-implement`.

Golden `*.IMPLEMENTATION.json` MUST validate:

```bash
node opsx_tools/opsx-apply-implement.mjs <golden.json>
```

Selection uses APPLY DESIGN fixture
`../opsx-apply-design/01-ready-valid-change.APPLY-DESIGN.json` and RED
`../opsx-apply-red/03-red-proven.RED.json` unless stated.

These fixtures do **not** authorize test execution. A correct executor MUST
NOT run `node --test` / `npm test` / unittest as part of IMPLEMENT.

## Index

| ID | File | Theme | Expected |
|----|------|-------|----------|
| 01 | `01-select-red-proven.md` | first RED_PROVEN without implement receipt | SLICE-001 |
| 02 | `02-write-boundary.md` | product yes, tests no | src/** ok; tests/** forbidden |
| 03 | `03-implementation-written.IMPLEMENTATION.json` | bounded production write | IMPLEMENTATION_WRITTEN, APPLY_GREEN |
| 04 | `04-missing-red.IMPLEMENTATION.json` | no RED_PROVEN | BLOCKED IMPLEMENTATION_AUTHORITY_MISSING |
| 05 | `05-stale-red.IMPLEMENTATION.json` | RED identity drift | BLOCKED STALE_RED |
| 06 | `06-stale-apply-design.IMPLEMENTATION.json` | design digest drift | BLOCKED STALE_APPLY_DESIGN |
| 07 | `07-dependency-not-green.IMPLEMENTATION.json` | downstream without GREEN | BLOCKED DEPENDENCY_NOT_GREEN |
| 08 | `08-design-reality-drift.IMPLEMENTATION.json` | architecture contradiction | BLOCKED DESIGN_REALITY_DRIFT |
| 09 | `09-scope-expansion.IMPLEMENTATION.json` | write outside expected surfaces | BLOCKED_SCOPE_EXPANSION |
| 10 | `10-new-production-file.IMPLEMENTATION.json` | design-required new file | IMPLEMENTATION_WRITTEN + unexpected_but_required |
| 11 | `11-frozen-red-mutated.IMPLEMENTATION.json` | tests changed during IMPLEMENT | BLOCKED FROZEN_RED_MUTATED |
| 12 | `12-no-test-execution.md` | no test subprocesses | tests_executed false |
| 13 | `13-apply-compat.md` | legacy apply still works | no IMPLEMENT gate |
| 14 | `14-no-green-behavior.md` | not GREEN/PASS; no repair loop | IMPLEMENTATION_WRITTEN only |
| 15 | `15-test-identity-before-after.md` | identity check is not execution | before == after == frozen |
| 16 | `16-semantic-current-slice-ok.md` | authorized path + current-slice behavior | semantic scope ok |
| 17 | `17-semantic-future-slice-behavior.IMPLEMENTATION.json` | authorized path + future-slice behavior | BLOCKED_SCOPE_EXPANSION |
| 18 | `18-reuse-duplication.IMPLEMENTATION.json` | REUSE silently duplicated | DESIGN_REALITY_DRIFT |
| 19 | `19-structural-seam-reserved.md` | structural seam ok; fill rejected | seam fill → BLOCKED |

## Static matrix

| Requirement | Coverage |
|-------------|---------|
| Deterministic RED_PROVEN slice | 01, tool `selectNextImplementableSlice` |
| Product code may be modified | 02, 03 |
| Frozen RED tests cannot be modified | 02, 11 |
| No test commands executed | 12, all goldens `tests_executed: false` |
| Terminal IMPLEMENTATION_WRITTEN not GREEN | 03, 14 |
| next_stage APPLY_GREEN | 03, 10 |
| Missing RED_PROVEN blocks | 04 |
| Stale RED blocks | 05 |
| Stale APPLY DESIGN blocks | 06 |
| Prior dep without GREEN blocks | 07 |
| Design/reality contradiction | 08 |
| Scope expansion blocks | 09, 17 |
| Semantic scope (not path-alone) | 16, 17, 19, tool `classifySemanticSliceScope` |
| REUSE must not silently duplicate | 18, tool `classifyReuseDecisionCompliance` |
| Legitimate new production file | 10 |
| Test identity before/after | 03, 11, 15 |
| No chat-history dependency | all goldens |
| Existing /opsx-apply operational | 13 |
| No APPLY GREEN embedded | 14 |
