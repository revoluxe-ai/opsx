---
description: "Schedule the next CURRENT check from a frozen bounded verification plan. Does not execute checks."
argument-hint: "change=<change-id>"
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


# /opsx-next-check

SOURCE command name for ledger scheduling. TARGET implementation is
`bounded_verify_ledger.schedule_write` under `.cursor/pipeline/bounded-verify/`.

## Role

**INTERFACE ONLY** — sole supported ledger writer is
`.cursor/pipeline/bounded-verify/bounded_verify_ledger.py`.

This command MUST invoke `bounded_verify_ledger.schedule_write(change_id, write=True)`
and MUST NOT write `ledger.json` directly.

## User input

```text
$ARGUMENTS
```

| Token | Required | Effect |
|-------|----------|--------|
| `change=<id>` | yes | OpenSpec change under `openspec/changes/<id>/` |

## Preconditions

- `plan.json` exists with `plan_status=FROZEN` and stable `plan_fingerprint`.
- Ledger (if present) binds the same `plan_id` / `plan_fingerprint`.
- `.scheduler.lock` serializes concurrent schedulers.

## Writes

| Path | Authority |
|------|-----------|
| `openspec/changes/<change_id>/bounded-verify/ledger.json` | `bounded_verify_ledger.schedule_write` only |
| `openspec/changes/<change_id>/bounded-verify/.scheduler.lock` | advisory lock during critical section |

Publication uses atomic temp sibling → validate → `os.replace`
(see `.cursor/pipeline/contracts/bounded-verify/ATOMIC_PUBLICATION.md`).

## Outputs

On success with a pending CURRENT check:

- Emits `CHECK_REQUIRED` with `check_id`, `assignment_epoch`, `ledger_revision`.
- Handoff command is `/bounded-executor mode=bounded change=<change_id>`.

When all checks are terminal:

- `NO_PENDING_CHECKS` and `ledger_status=COMPLETE`.

## Does NOT

- Mutate `plan.json`.
- Execute static checks or registry runners.
- Write `results/*.json`.
- Broaden plan WHAT beyond frozen `required_checks`.
- Emit archive `GO` / `NO_GO` / aggregate PASS.
- Write `openspec/changes/<id>/verify/VERIFICATION-LEDGER.json` (SOURCE carrier; not this TARGET).

## Fail-closed codes

| Code | Meaning |
|------|---------|
| `PLAN_NOT_FROZEN` | Plan absent, DRAFT, or invalid |
| `LEDGER_STALE_PLAN_IDENTITY` | Ledger bound to a different plan fingerprint |
| `LEDGER_STALE_REVISION` | CAS `expected_revision` mismatch |
| `CHECK_NOT_IN_PLAN` | Ledger entry check_id not in the frozen plan |
| `MULTIPLE_CURRENT` | Schema violation: more than one CURRENT |
| `RESULT_STALE_*` | Drain rejected a stale result |

## Steps

1. Parse `change=<id>`; fail closed if missing.
2. Invoke from the TARGET repo root:

```powershell
poetry run python .cursor\pipeline\bounded-verify\bounded_verify_ledger.py schedule --change <change_id> --write
```

3. Report `CHECK_REQUIRED` or `NO_PENDING_CHECKS` with revision and assignment.
4. On `CHECK_REQUIRED`: next command is `/bounded-executor mode=bounded change=<change_id>`.

## Authority references

- `.cursor/pipeline/contracts/bounded-verify/AUTHORITY_SPLIT.md`
- `.cursor/pipeline/contracts/bounded-verify/LEDGER_SCHEMA.json`
- `AT_MOST_ONE_CURRENT` enforced by schema and scheduler invariants.

## Language

Пояснения — русский. Коды ошибок и пути — английский.
