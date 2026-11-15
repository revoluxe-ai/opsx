# Fixtures: /opsx-apply-green

Process-only regression cases. Command: `/opsx-apply-green`.

Golden `*.GREEN.json` MUST validate:

```bash
node opsx_tools/opsx-apply-green.mjs <golden.json>
```

Selection uses APPLY DESIGN fixture
`../opsx-apply-design/01-ready-valid-change.APPLY-DESIGN.json`, RED
`../opsx-apply-red/03-red-proven.RED.json`, IMPLEMENT
`../opsx-apply-implement/03-implementation-written.IMPLEMENTATION.json`
unless stated.

`GREEN_PROVEN` is slice-local. It is not change-level verification PASS.

Harness files `harness-pass.test.mjs` / `harness-fail.test.mjs` exist only so
contract tests can spawn `runFrozenRedCommand`. They are not product tests.

## Index

| ID | File | Theme | Expected |
|----|------|-------|----------|
| 01 | `01-select-implement-written.md` | first RED+IMPLEMENT without GREEN | SLICE-001 |
| 02 | `02-red-executed.md` | frozen RED actually executed | subprocess, not agent claim |
| 03 | `03-green-proven-immediate.GREEN.json` | RED pass → adjacent → GREEN | GREEN_PROVEN, APPLY_SCAFFOLD |
| 04 | `04-fix-loop-then-green.GREEN.json` | implementation bug then pass | GREEN_PROVEN, 2 fixes |
| 05 | `05-frozen-red-mutated.GREEN.json` | tests changed | FROZEN_RED_MUTATED |
| 06 | `06-env-failure.GREEN.json` | env fail, no product loop | ENVIRONMENT_FAILURE |
| 07 | `07-unrelated.GREEN.json` | unrelated, no scope expand | UNRELATED_FAILURE |
| 08 | `08-design-change-required.GREEN.json` | architecture change needed | DESIGN_CHANGE_REQUIRED |
| 09 | `09-iteration-limit.GREEN.json` | 5 failed repairs | GREEN_ITERATION_LIMIT |
| 10 | `10-no-progress.GREEN.json` | same signature+digest | GREEN_NO_PROGRESS |
| 11 | `11-adjacent-repair.GREEN.json` | adjacent slice regression | GREEN_PROVEN after RED rerun |
| 12 | `12-apply-complete.GREEN.json` | last slice | APPLY_COMPLETE |
| 13 | `13-dependency-not-green.GREEN.json` | 003 without 001/002 GREEN | DEPENDENCY_NOT_GREEN |
| 14 | `14-apply-compat.md` | legacy apply | no GREEN gate |
| 15 | `15-not-whole-change.md` | not verify PASS | whole_change_verified false |
| 16 | `16-bounded-adjacent.md` | no full suite | bounded only |
| 17 | `17-stale-green-after-implement-regen.md` | IMPLEMENT regen | STALE_GREEN; re-eligible APPLY_GREEN |
| 18 | `18-adjacent-alias-not-not-required.md` | relevant + plain-node `@/` miss | not NOT_REQUIRED; use alias loader |
| 19 | `19-unrelated-adjacent-not-required.md` | no surface overlap | NOT_REQUIRED from semantics |
| 20 | `20-runner-unavailable.GREEN.json` | required check, no runner | BLOCKED TEST_INFRA_FAILURE |

