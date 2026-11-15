# Prep-green-scope runtime artifacts

Path: `opsx/prep-green-scope/`

## Schema v2 (current)

| File | Schema id |
|------|-----------|
| `probe-result.json` | `opsx.prep_green_scope.probe.v2` |
| `plan.json` | `opsx.prep_green_scope.plan.v2` |

Required identity fields:

- `command_contract_id`: `OPSX-PREP-GREEN-SCOPE-V1`
- `command_contract_version`: matches `contract.py` (e.g. `0.9.1`)
- `static_check_identity`: SHA256 prefix of `registry.yaml` + `synthesis.md` (see `prep_green_full_handoff.compute_static_check_identity`)
- `probe_execution_identity`: hash of static-check identity + declared `destructive_policy` + `env_class`
- `probe_execution_identity_detail.full_fingerprint`: worktree fingerprint from FULL handoff

`artifact_is_stale()` rejects `static_check_identity` / `probe_execution_identity` mismatch.

## STALE / INVALID_FOR_CURRENT_CONTRACT

Reject for GO/APPLY when any:

- `schema` not `*.v2`
- `command_contract_version` mismatch
- `static_check_identity` missing or mismatch vs current repo registry hash
- `probe_execution_identity` mismatch vs current destructive policy / env class
- `will_apply.pytest_deselect_count`, `mypy_ignore_errors_global`
- `gate_mapping_note` or `notes` mention `--deselect` or «SKIPPED integration/destructive after unit FAIL»
- `implementation` = `stroy-prep-green-scope-v3.0.0-agent` without v2 fields

Use `artifacts.artifact_is_stale()`.

## plan.json highlights

- `status`: `AWAITING_GO` | `BLOCKED_INCOMPLETE_PROBE` | `BLOCKED_INCOMPLETE_HANDOFF` | `BLOCKED_MISSING_FULL_HANDOFF`
- `go_forbidden`: true when GO not allowed
- `disable_plan`: `{ pyright_files, mypy_modules, pytest_nodes }`
- `will_apply`: counts only (`pytest_source_skip_count`, no deselect count)

Writer: `artifacts.write_probe_and_plan` after STAGE1.
