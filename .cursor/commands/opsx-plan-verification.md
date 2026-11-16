---
description: "Freeze a finite bounded verification plan for an OpenSpec change. Does not execute checks."
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


# /opsx-plan-verification

SOURCE command name for verification-plan freeze. TARGET implementation is
`bounded_verify_plan.freeze_plan` under `.cursor/pipeline/bounded-verify/`.

## Role

**INTERFACE ONLY** — sole supported write authority is
`.cursor/pipeline/bounded-verify/bounded_verify_plan.py`.

This command MUST invoke `bounded_verify_plan.freeze_plan(change_id)` and MUST
NOT write `plan.json` directly.

## User input

```text
$ARGUMENTS
```

| Token | Required | Effect |
|-------|----------|--------|
| `change=<id>` | yes | OpenSpec change under `openspec/changes/<id>/` |

## Preconditions

- DRAFT plan published via `/opsx-compose-verification-plan` or `bounded_verify_plan.draft_plan`.
- Impl-complete signal: legacy `tasks.md` with `[x]`, **or** split-APPLY `IMPL_OK` from
  `opsx-apply-verify-completion.mjs` (unchecked tasks.md allowed).
- Each `required_checks[].check_id` exists in `.cursor/static-check/registry.yaml`
  (read-only validation).

## Writes

| Path | Authority |
|------|-----------|
| `openspec/changes/<change_id>/bounded-verify/plan.json` | `bounded_verify_plan.freeze_plan` only |

Publication uses atomic temp sibling → validate → `os.replace`
(see `.cursor/pipeline/contracts/bounded-verify/ATOMIC_PUBLICATION.md`).

## Does NOT

- Execute static checks or pytest.
- Emit archive `GO` / `NO_GO` / aggregate PASS.
- Schedule ledger CURRENT.
- Accept `--force`, `REGENERATE`, or overwrite-frozen.
- Write `openspec/changes/<id>/verify/VERIFICATION-PLAN.json` (SOURCE carrier; not this TARGET).

## Fail-closed codes

| Code | Meaning |
|------|---------|
| `PLAN_FROZEN_MUTATION_REFUSED` | `draft_plan` after FROZEN; freeze itself is an idempotent no-op |
| `FREEZE_BLOCKED_GAPS` | No DRAFT plan or blocked preconditions |
| `REGISTRY_CHECK_UNKNOWN` | `check_id` not in registry.yaml |
| `TASKS_INCOMPLETE` | tasks.md missing impl-complete signal |

## Steps

1. Parse `change=<id>`; fail closed if missing.
2. Invoke Python API from the TARGET repo root:

```powershell
poetry run python -c "from pathlib import Path; import sys; sys.path.insert(0, str(Path('.cursor/pipeline/bounded-verify'))); from bounded_verify_plan import freeze_plan; fp = freeze_plan('<change_id>'); print(f'PLAN_FROZEN plan_id={fp.plan_id} fingerprint={fp.plan_fingerprint}')"
```

3. Report `plan_id`, `plan_fingerprint`, `required_checks` count.
4. On success: next command is `/opsx-next-check change=<change_id>`.

## Authority references

- `.cursor/pipeline/contracts/bounded-verify/AUTHORITY_SPLIT.md`
- `.cursor/pipeline/contracts/bounded-verify/PLAN_SCHEMA.json`
- `plan_fingerprint = sha256(canonical_json_frozen_subset)` — fields:
  `schema_version`, `change_id`, `plan_id`, `plan_status`, `required_checks`,
  `implementation_snapshot.content_digest` (no git HEAD).

## Language

Пояснения — русский. Коды ошибок и пути — английский.
