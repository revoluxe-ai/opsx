---
description: "Execute only the ledger CURRENT bounded check. Requires mode=bounded."
argument-hint: "mode=bounded change=<change-id> [check_id=<id>] [assignment_epoch=<n>] [ledger_revision=<n>]"
---


# PROJECT TARGET PROFILE — REQUIRED

Product-specific execution authority resolves only through:

  opsx/project/capabilities.yaml

Adapter-specific supplements apply only when that adapter is selected in
capabilities.yaml (see opsx/adapters/<adapter-id>/).

Donor literals in this command are historical compatibility text, not authority.
Before product-specific reads, writes, or commands, resolve the required capability.
If unresolved, fail closed with TARGET_CAPABILITY_UNRESOLVED.

Control-plane paths under tools/temporal and tools/cursor_executor are separate
from product runtime adoption.


# /bounded-executor

SOURCE capability token for CURRENT-only bounded execution. TARGET implementation
is `bounded_verify_executor.run_current_check` under
`.cursor/pipeline/bounded-verify/`.

This is not `/opsx-static-check` and not `/opsx-verify`. Those commands stay
on their existing paths.

## Role

**INTERFACE ONLY** — sole supported CURRENT executor is
`.cursor/pipeline/bounded-verify/bounded_verify_executor.py`.

This command MUST invoke `bounded_verify_executor.run_current_check(change_id)`
and MUST NOT write `ledger.json`, `plan.json`, or `registry.yaml`.

## User input

```text
$ARGUMENTS
```

| Token | Required | Effect |
|-------|----------|--------|
| `mode=bounded` | yes | Exact scope token. Any other mode is refused before execution. |
| `change=<id>` | yes | OpenSpec change under `openspec/changes/<id>/` |
| `check_id=<id>` | no | If present, must equal ledger CURRENT. Mismatch → `ASSIGNMENT_NOT_CURRENT`. |
| `assignment_epoch=<n>` | no | If present, must equal CURRENT epoch. Mismatch → `ASSIGNMENT_MISMATCH`. |
| `ledger_revision=<n>` | no | If present, must equal ledger revision. Mismatch → `ASSIGNMENT_MISMATCH`. |

## Scope refusal

If `mode=bounded` is absent, or `mode` is any other value:

```text
STATUS: BLOCKED
REASON: MODE_NOT_BOUNDED
```

Do not call the executor. Do not run `.cursor/static-check` packs.

## Preconditions

- `plan.json` exists with `plan_status=FROZEN`.
- `ledger.json` exists with `ledger_status=ACTIVE` and exactly one `CURRENT`.
- `current_assignment` has `check_id` and `assignment_epoch`.
- Ledger binds the same `plan_id` / `plan_fingerprint` as the frozen plan.

## Writes

| Path | Authority |
|------|-----------|
| `openspec/changes/<change_id>/bounded-verify/results/<result_id>.json` | `bounded_verify_result.create_result` via executor only |

Publication uses exclusive create (`O_CREAT|O_EXCL`). Partial files are not canonical.

## Outputs

On success:

- `RESULT_RECORDED` with `check_id`, `result_id`, `result_status`, `execution_id`.

Result `PASS` is execution evidence only. It is not archive `GO` and not
aggregate verification PASS.

## Does NOT

- Schedule the ledger or select CURRENT.
- Mutate `plan.json` or `ledger.json`.
- Mutate `.cursor/static-check/registry.yaml`.
- Run `/opsx-static-check` FULL or FAST waves.
- Emit archive `GO` / `NO_GO` / aggregate PASS.
- Retry with the same `execution_id` (collision → `RESULT_ALREADY_EXISTS`).

## Fail-closed codes

| Code | Meaning |
|------|---------|
| `MODE_NOT_BOUNDED` | `mode=bounded` missing or different |
| `ASSIGNMENT_NOT_CURRENT` | No CURRENT, or requested `check_id` is not CURRENT |
| `ASSIGNMENT_MISMATCH` | Stale `assignment_epoch` or `ledger_revision` |
| `LEDGER_STALE_PLAN_IDENTITY` | Ledger/plan identity mismatch |
| `RESULT_ALREADY_EXISTS` | Duplicate `execution_id` / `result_id` |
| `EXECUTOR_BLOCKED` | Registry or runner blocked |
| `REGISTRY_CHECK_UNKNOWN` | `check_id` not in registry.yaml |

## Steps

1. Require exact token `mode=bounded`. Otherwise stop with `MODE_NOT_BOUNDED`.
2. Parse `change=<id>`; fail closed if missing.
3. Invoke from the TARGET repo root. Pass optional binds only when the user supplied them:

```powershell
poetry run python .cursor\pipeline\bounded-verify\bounded_verify_executor.py run --mode bounded --change <change_id>
```

Optional flags: `--check-id <check_id> --assignment-epoch <n> --ledger-revision <n>`.

4. Report `RESULT_RECORDED` with ids and status.
5. On success: next command is `/opsx-next-check change=<change_id>` to drain the result and advance the ledger.

HOW lookup stays read-only: `.cursor/static-check/registry.yaml` and the check pack `instructions.md`.

## Authority references

- `.cursor/pipeline/contracts/bounded-verify/AUTHORITY_SPLIT.md`
- `.cursor/pipeline/contracts/bounded-verify/RESULT_SCHEMA.json`

## Language

Пояснения — русский. Коды ошибок и пути — английский.
