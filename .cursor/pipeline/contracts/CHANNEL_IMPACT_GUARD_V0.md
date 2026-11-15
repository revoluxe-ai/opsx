# Channel Impact Guard V0

`LIVE_AUTHORITY: NO` — planning, verify, and CI enforcement via Python gate only.

## Purpose

Single policy for **registered customer channels** (telegram, max, avito). One
validation module (`.cursor/pipeline/channel-impact/channel_impact_guard.py`)
is invoked at:

1. **Planning freeze** — `assert_planning_ready` / `bounded_verify_plan.freeze_plan`
   (`planning-gate`, `openspec_guard --guard-stage planning_readiness` for
   `opsx_ff` / `opsx_update` / `opsx_continue`; early writes use `--guard-stage early`)
2. **Archive verification** — `/opsx-verify` policy + machine `archive_gate`
   (`verify-gate`, `tools/opsx/verify_channel_impact_gate.py`,
   `openspec_guard --scenario opsx_verify_archive`)
3. **Post-merge CI** — `.github/workflows/opsx-channel-impact-ci.yml` (`ci-gate` on
   `push` to `main` / `stage` only; no `pull_request` triggers)

No separate `/opsx-channel-impact` command.

## Registry source

`opsx/project/capabilities.yaml` → `channel_impact` (exported to JSON).

## Change artifact

`openspec/changes/<change_id>/channel-impact-guard.json` (`schema_version`:
`channel-impact-guard-v0`).

Required fields when published:

- `applicability`: `APPLICABLE` | `NOT_APPLICABLE` | `NO_CHANGE`
- `declared_semantics`: `{ "channels": [...], "scope_summary": "..." }`
- `no_change_justification`: required when `NO_CHANGE` (non-empty)
- `product_acceptance`: `{ "required": bool, "status": "PASS"|"PENDING"|"NOT_REQUIRED", "scenario_evidence_refs": [...] }`

## Fail-closed rules

1. Diff touches channel **surfaces** (registry globs) but no OpenSpec change contour → `UNRESOLVED_NO_CHANGE_CONTOUR`.
2. Declared `NOT_APPLICABLE` or `NO_CHANGE` while surfaces changed → `DECLARATION_MISMATCH`.
3. `NO_CHANGE` without justification → `NO_CHANGE_UNJUSTIFIED`.
4. `APPLICABLE` functional work: `product_acceptance.status` must be `PASS` and `scenario_evidence_refs` non-empty (matrix alone insufficient per Owner D-CIG-3).
5. Unresolved applicability (missing artifact) at freeze or CI → `UNRESOLVED_APPLICABILITY`.

## Verify policy

`openspec/policies/VERIFY_POLICY.md` → `archive_blockers` includes
`channel_impact_guard_not_pass`.
