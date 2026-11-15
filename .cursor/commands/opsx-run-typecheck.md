---
description: "Bounded execute-only typecheck facade for frozen pyright-strict/mypy CURRENT. No repairs."
argument-hint: "mode=bounded change=<change-id> [check_id=<id>] [assignment_epoch=<n>] [ledger_revision=<n>]"
contract_id: OPSX-WB-RUN-TYPECHECK-01
---

# STROY-SYSTEM TARGET OVERRIDE - HIGHEST PRIORITY

This command was transferred from a project-specific OPSX realization.
For stroy-system, product-specific realization MUST resolve through:

  opsx/project/capabilities.yaml
  opsx/shared/target-capability-resolution.md

Any inherited donor literal (WB Sky, adv-bot, Poetry, pyproject.toml, app/**,
tests/**, npm/package.json, src/**, public/**, deploy names) is historical or
compatibility text only and is NOT execution authority.

Before any product-specific read/write/command execution, resolve the
corresponding TARGET capability. If unresolved, fail closed:

STATUS: FAIL_CLOSED
REASON: TARGET_CAPABILITY_UNRESOLVED
CAPABILITY: <required capability>
NEXT_ACTION: define/discover capability in opsx/project/capabilities.yaml

Do not infer product stack from the donor repository.
Control-plane runtime paths under tools/temporal and tools/cursor_executor are
separate from product Temporal/runtime adoption.

# /opsx-run-typecheck

SOURCE command name for **atomic execute-only** Python type diagnostics during
frozen S6 bounded verification. TARGET maps SOURCE `type_check` capability to
registry checks `pyright-strict` and `mypy` only.

```text
IMPLEMENTATION_STATUS: IMPLEMENTED_UNVERIFIED
ARCHITECTURAL_ROLE: ATOMIC_EXECUTOR
FIX_TYPES: NO
GREEN_WRITER: NO
LEDGER_WRITER: NO
RESULT_WRITER: NO
SOURCE_PARITY: PARTIAL
```

## Role

**INTERFACE ONLY — bounded typecheck facade.** This command validates
typecheck-eligible CURRENT assignment, then delegates execution to the sole
bounded runner (`/bounded-executor` →
`bounded_verify_executor.run_current_check`). It is not a second executor and
does not publish `results/*.json` itself.

Preserves SOURCE semantics: deterministic diagnostics only, no type repairs, no
archive `GO`, no aggregate PASS self-claim.

## User input

```text
$ARGUMENTS
```

| Token | Required | Effect |
|-------|----------|--------|
| `mode=bounded` | yes | Exact scope token. Any other mode → `MODE_NOT_BOUNDED`. |
| `change=<id>` | yes | OpenSpec change under `openspec/changes/<id>/` |
| `check_id=<id>` | no | If present, must equal ledger CURRENT **and** be `pyright-strict` or `mypy`. |
| `assignment_epoch=<n>` | no | If present, must equal CURRENT epoch. Mismatch → `ASSIGNMENT_MISMATCH`. |
| `ledger_revision=<n>` | no | If present, must equal ledger revision. Mismatch → `ASSIGNMENT_MISMATCH`. |

### Refused invocation shapes (fail closed)

| User input | Code |
|------------|------|
| `mode` absent or not exactly `bounded` | `MODE_NOT_BOUNDED` |
| `change=` absent | `CHANGE_REQUIRED` |
| `typecheck_step=…` or other loose explicit recipe | `BINDING_GAP` |
| Request to run `/opsx-static-check` FULL/FAST or ad-hoc `poetry run pyright` / `mypy` outside frozen CURRENT | `BINDING_GAP` |
| CURRENT `check_id` is `pytest-targeted`, `build`, `tests`, or unknown | `ASSIGNMENT_NOT_CURRENT` or `BINDING_GAP` |
| CURRENT is not `pyright-strict` or `mypy` when this command is invoked | `ASSIGNMENT_NOT_CURRENT` |
| Requested `check_id` ≠ ledger CURRENT | `ASSIGNMENT_NOT_CURRENT` |

Manual SOURCE diagnostic path `typecheck_step=<frozen-step>` is **not**
supported on this target; use frozen-plan CURRENT via this facade or
`/bounded-executor` when CURRENT is already a typecheck check.

## Typecheck-eligible frozen checks (TARGET)

Read-only registry evidence: `.cursor/static-check/registry.yaml` defines
`pyright-strict` and `mypy` packs. Frozen S6 plans (for example
`s6-bounded-verify-activation`) declare only those two typecheck steps in
`required_checks`.

```text
TYPECHECK_ELIGIBLE: pyright-strict | mypy
```

## Preconditions (read before delegate)

1. `plan.json` with `plan_status=FROZEN`.
2. `ledger.json` with `ledger_status=ACTIVE` and exactly one `CURRENT`.
3. Ledger binds the same `plan_id` / `plan_fingerprint` as the frozen plan.
4. CURRENT `check_id` ∈ {`pyright-strict`, `mypy`}.
5. If user supplied `check_id`, it equals CURRENT and is typecheck-eligible.

## Execution delegate (sole runner)

After gates pass, invoke **only** the bounded executor CLI (same flags the user
supplied for optional binds):

```powershell
poetry run python .cursor\pipeline\bounded-verify\bounded_verify_executor.py run --mode bounded --change <change_id>
```

Optional passthrough: `--check-id <check_id> --assignment-epoch <n> --ledger-revision <n>`.

Equivalent handoff name (same semantics):

```text
/bounded-executor mode=bounded change=<change_id> [check_id=…] [assignment_epoch=…] [ledger_revision=…]
```

Do **not** call `bounded_verify_executor` with a different `check_id` than
validated CURRENT. Do **not** write a second result record.

## Writes

| Path | Authority |
|------|-----------|
| `openspec/changes/<change_id>/bounded-verify/results/<result_id>.json` | `bounded_verify_executor` / `bounded_verify_result.create_result` only |

This command MUST NOT write `plan.json`, `ledger.json`, or
`.cursor/static-check/registry.yaml`.

## Outputs

On successful delegate (executor accepted run):

```text
RESULT_RECORDED
check_id=
result_id=
result_status=
execution_id=
NEXT_COMMAND: /opsx-next-check change=<change_id>
```

`result_status=PASS` is execution evidence only — not archive `GO`, not
ledger advance, not type repair success for downstream GREEN.

On executor refusal, surface the executor code unchanged (`MODE_NOT_BOUNDED`,
`ASSIGNMENT_NOT_CURRENT`, `ASSIGNMENT_MISMATCH`, `LEDGER_STALE_PLAN_IDENTITY`,
`RESULT_ALREADY_EXISTS`, `EXECUTOR_BLOCKED`, `REGISTRY_CHECK_UNKNOWN`, …).

## Does NOT

- Schedule ledger CURRENT (`/opsx-next-check` only).
- Mutate `plan.json` or `ledger.json`.
- Run `/opsx-fix-types` or edit `app/**`, `tests/**` for repairs.
- Run `/opsx-static-check` waves or bypass frozen plan with explicit commands.
- Mark PASS without executor `RESULT_RECORDED`.
- Claim full SOURCE parity for vue-npm `typecheck_step` recipes
  (`TYPECHECK_RECIPE_GAP` on game target).

## Fail-closed codes (facade layer)

| Code | Meaning |
|------|---------|
| `MODE_NOT_BOUNDED` | `mode=bounded` missing or different |
| `CHANGE_REQUIRED` | `change=<id>` missing |
| `BINDING_GAP` | Non-frozen recipe, wrong capability, or non-typecheck CURRENT family |
| `ASSIGNMENT_NOT_CURRENT` | No CURRENT, wrong check, or typecheck command while CURRENT is not pyright/mypy |
| `ASSIGNMENT_MISMATCH` | Stale `assignment_epoch` or `ledger_revision` |
| `LEDGER_STALE_PLAN_IDENTITY` | Ledger/plan identity mismatch (from executor) |

## Steps

1. Require exact token `mode=bounded`. Otherwise `MODE_NOT_BOUNDED` — do not delegate.
2. Parse `change=<id>`; fail closed if missing (`CHANGE_REQUIRED`).
3. Read frozen `plan.json` and ACTIVE `ledger.json`; verify CURRENT
   `check_id` is `pyright-strict` or `mypy`. Otherwise stop
   (`ASSIGNMENT_NOT_CURRENT` or `BINDING_GAP` for build/tests/pytest CURRENT).
4. If `check_id=` present, verify equality with CURRENT and typecheck eligibility.
5. If `typecheck_step=` or static-check bypass tokens appear, stop `BINDING_GAP`.
6. Delegate to `bounded_verify_executor.py run` as above; report executor output.
7. On `RESULT_RECORDED`: next command is `/opsx-next-check change=<change_id>`.

## Authority references

- `.cursor/pipeline/contracts/bounded-verify/AUTHORITY_SPLIT.md`
- `.cursor/pipeline/contracts/bounded-verify/RESULT_SCHEMA.json`
- `.cursor/commands/bounded-executor.md` (sole CURRENT runner)
- `.cursor/commands/opsx-next-check.md` (sole ledger writer)

## Language

Пояснения — русский. Коды ошибок и пути — английский.
