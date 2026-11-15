# Fixtures: /opsx-apply-red

Process-only regression cases. Command: `/opsx-apply-red`.

Golden `*.RED.json` that claim `RED_PROVEN` MUST validate:

```bash
node opsx_tools/opsx-apply-red.mjs <golden.json>
```

`19-incomplete-old-v1.RED.json` is a **negative** fixture (schema v1, one
execution for four commands). It MUST NOT validate and MUST NOT be treated as
durable `RED_PROVEN`.

Selection uses APPLY DESIGN fixture
`../opsx-apply-design/01-ready-valid-change.APPLY-DESIGN.json` unless stated.

## Index

| ID | File | Theme | Expected |
|----|------|-------|----------|
| 01 | `01-select-first-eligible.md` | first eligible slice deterministic | SLICE-001; other SLICE-002 |
| 02 | `02-write-boundary.md` | tests yes, product no | no src/** writes |
| 03 | `03-red-proven.RED.json` | one command, one qualified execution | RED_PROVEN, APPLY_IMPLEMENT, tests_frozen |
| 04 | `04-setup-failure.RED.json` | syntax/setup | BLOCKED, not RED_PROVEN |
| 05 | `05-repair-then-proven.RED.json` | self-repair attempts then INTENDED | RED_PROVEN |
| 06 | `06-already-satisfied.RED.json` | pass before impl | BLOCKED ALREADY_SATISFIED |
| 07 | `07-dependency-not-ready.md` | downstream without GREEN | BLOCKED_DEPENDENCY_NOT_READY |
| 08 | `08-deterministic-multi.md` | two independent eligible | pick 001, report 002 |
| 09 | `09-stale-apply-design.md` | stale/invalid design | BLOCKED STALE_OR_INVALID |
| 10 | `10-apply-compat.md` | legacy apply still works | no RED gate |
| 11 | `11-sentinel-proven.RED.json` | scaffold sentinel through seam | RED_PROVEN |
| 12 | `12-missing-import-not-red.RED.json` | wrong import | BLOCKED SCAFFOLD_DEFECT |
| 13 | `13-missing-symbol-not-red.RED.json` | missing symbol | BLOCKED SCAFFOLD_DEFECT |
| 14 | `14-no-product-writes.md` | RED cannot edit product/scaffold | SCAFFOLD_FILES_MODIFIED false |
| 15 | `15-four-commands-all-qualified.RED.json` | four executed + qualified | RED_PROVEN |
| 16 | `16-three-qualify-one-setup.RED.json` | three qualify, one syntax | BLOCKED, tests_frozen false |
| 17 | `17-repair-fourth-then-proven.RED.json` | repair fourth then all qualify | RED_PROVEN; identity after repair |
| 18 | `18-wrong-pattern-zero-tests.RED.json` | wrong --test-name-pattern | BLOCKED TEST_SETUP_FAILURE |
| 19 | `19-incomplete-old-v1.RED.json` | old RED_PROVEN, one of four executed | not durable; regenerate |
| 20 | (unit) `opsxApplyVerifyCompletionContract.test.mjs` | planned artifacts missing with slice context | `MISSING_PLANNED_TEST_ARTIFACT`; goldens still PASS without slice |

## Static matrix

| Requirement | Coverage |
|-------------|---------|
| Deterministic first slice | 01, 08, tool `selectNextEligibleSlice` |
| Writes tests not product | 02, 03 `product_files_modified` |
| One command qualified → RED_PROVEN | 03 |
| Four commands all executed + qualified | 15 |
| Four commands, only one executed | 19 (negative) + validator |
| Three qualify, one setup failure | 16 |
| Repair fourth then freeze | 17 |
| Setup failure not PROVEN | 04 |
| Repair own tests and retry | 05, 17 |
| Already passing → ALREADY_SATISFIED | 06 |
| Unmet dep | 07 |
| Test content identity + freeze after final repair | 03, 17 |
| next_stage APPLY_IMPLEMENT only if PROVEN | 03 vs 04/06/16 |
| Sentinel through intended seam | 11 |
| Wrong import / missing symbol | 12, 13 |
| Incorrect test-name-pattern / zero tests | 18 |
| Incomplete old receipt regenerated | 19 |
| No chat history | all goldens |
| Apply operational | 10 |
| Stale APPLY DESIGN | 09 |
