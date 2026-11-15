---
description: "Execute-only explicit Poetry package build (manual) or refuse bounded until S6 wiring."
argument-hint: "build_recipe=poetry_package output_dir=.cursor/pipeline/run-build/artifacts/<name>"
contract_id: OPSX-WB-RUN-BUILD-01
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

# /opsx-run-build

SOURCE command name for **atomic execute-only** package builds. TARGET maps frozen
S6 verification PASS to existing `bounded-verify` + `/bounded-executor`; this
command owns **manual explicit Poetry package build** only.

```text
IMPLEMENTATION_STATUS: IMPLEMENTED_UNVERIFIED
RUNTIME_STATUS: ACTIVE_WITH_LIMITS
CONTRACT_ID: OPSX-WB-RUN-BUILD-01
ARCHITECTURAL_ROLE: ATOMIC_EXECUTOR
GREEN_WRITER: NO
TEST_AUTHORING: NO
LEDGER_WRITER: NO
RESULT_WRITER: NO
SOURCE_PARITY: PARTIAL
```

## Role

Deterministic **execute-only** runner for Owner debug and an explicit
`poetry_package` recipe when `pyproject.toml` declares the Poetry build backend.
It never invents frontend/npm builds, defaults an implicit build, or writes
durable verification PASS.

| Mode | Authority |
|------|-----------|
| `mode=bounded` | **BLOCKED** — durable PASS remains S6 `bounded_verify_executor` only |
| `build_recipe=poetry_package` + `output_dir=` | Chat-only `RUN_RESULT` via `.cursor/pipeline/run-build/run_build.py` |

Preserves SOURCE separation: execution here; `/opsx-static-check` remains the
umbrella FAST/FULL matrix; vue-npm `frontend_build` is **UNAVAILABLE** on this
`poetry-python` tree (see `opsx/project/quality-gates.md`).

## User input

```text
$ARGUMENTS
```

| Token | Required | Effect |
|-------|----------|--------|
| `build_recipe=poetry_package` | yes (manual path) | Only supported manual recipe on this target |
| `output_dir=<relative>` | yes (manual path) | Must be under `.cursor/pipeline/run-build/artifacts/`; never repo-root `dist/` |
| `mode=bounded` | bounded attempt | Always `BLOCKED` `BINDING_GAP` (`S6_PACKAGE_BUILD_CAPABILITY_UNREGISTERED`) |

### Refused invocation shapes (fail closed)

| Input | STATUS | reason |
|-------|--------|--------|
| `build_recipe` absent | BLOCKED | `BUILD_RECIPE_REQUIRED` |
| `build_recipe=npm_run_build` | BLOCKED | `EXECUTOR_ADAPTER_GAP` |
| `output_dir` absent for `poetry_package` | BLOCKED | `OUTPUT_DIR_REQUIRED` |
| `output_dir` outside owned artifacts prefix | BLOCKED | `OUTPUT_DIR_NOT_OWNED` |
| Traversal / absolute `output_dir` | BLOCKED | `PATH_TRAVERSAL` / `OUTPUT_DIR_ABSOLUTE_FORBIDDEN` |
| Missing / non-Poetry `pyproject.toml` | BLOCKED | `POETRY_PROJECT_MISSING` / `POETRY_BUILD_BACKEND_MISSING` |
| `mode=bounded` (any fields) | BLOCKED | `BINDING_GAP` |
| Non-zero `poetry build` | FAIL | `BUILD_FAILED` |
| Runner crash | BLOCKED | `EXECUTION_ERROR` |

## Manual execution helper (sole runner for manual recipe)

After gates pass, invoke **only**:

```powershell
poetry run python .cursor\pipeline\run-build\run_build.py --repo-root . --arguments "build_recipe=poetry_package output_dir=.cursor/pipeline/run-build/artifacts/<name>"
```

The helper:

- requires provable `poetry.core.masonry.api` in root `pyproject.toml`;
- runs `poetry build --output <owned_dir> --no-interaction` (never default `dist/`);
- emits `RUN_RESULT` text — **not** durable verification evidence.

Do **not** run deployment, Kubernetes, or `/opsx-static-check` waves from this
command.

## Writes

| Path class | Authority |
|------------|-----------|
| `app/**`, `tests/**`, product code | **NONE** |
| `openspec/changes/**/bounded-verify/results/**` | **NONE** (use `/bounded-executor`) |
| `VERIFICATION-LEDGER.json`, plan, bindings (SOURCE carriers) | **NONE** |
| `.cursor/pipeline/run-build/artifacts/**` | manual `poetry_package` wheel/sdist output only |
| Chat `RUN_RESULT` | manual mode only; `DURABLE_RECEIPT: NONE` |

Manual PASS is **not** archive `GO`, ledger advance, or bounded verification PASS.

## Bounded mode (explicit refusal)

When `$ARGUMENTS` contains `mode=bounded`:

1. Do **not** execute a build.
2. Do **not** write `BOUNDED_CHECK_RESULT` or `results/*.json`.
3. Emit `STATUS: BLOCKED` `reason: BINDING_GAP` with detail
   `S6_PACKAGE_BUILD_CAPABILITY_UNREGISTERED`.
4. `NEXT_ACTION`: run frozen checks through
   `/bounded-executor mode=bounded change=<change_id>` when CURRENT is a
   registered S6 check — not through this command.

SOURCE game `frontend_build` / `npm_run_build` and
`opsx_tools/opsx-run-build.mjs` overlay materialization are **not** ported.

## Failure model (manual helper)

| Condition | STATUS | reason |
|-----------|--------|--------|
| Missing recipe / output | BLOCKED | `BUILD_RECIPE_REQUIRED` / `OUTPUT_DIR_REQUIRED` |
| vue-npm recipe | BLOCKED | `EXECUTOR_ADAPTER_GAP` |
| Output outside owned prefix | BLOCKED | `OUTPUT_DIR_NOT_OWNED` |
| Poetry backend not provable | BLOCKED | `POETRY_BUILD_BACKEND_MISSING` |
| Build non-zero | FAIL | `BUILD_FAILED` |

## Does NOT

- Author or edit production/tests.
- Run RED/GREEN workflows or write bug evidence.
- Mutate Verification Plan, ledger, bindings, or bounded-verify `results/**`.
- Claim bounded verification PASS without S6 executor `RESULT_RECORDED`.
- Substitute for `/opsx-static-check` FAST/FULL waves.
- Port SOURCE `PINNED_BASE_PLUS_FROZEN_OVERLAY` npm candidate builds.

## Output format (manual)

```text
CONTRACT_ID: OPSX-WB-RUN-BUILD-01
MODE: manual
STATUS: PASS | FAIL | BLOCKED
reason:
detail:
build_recipe:
output_dir:
resolved_command:
exit_code:
DURABLE_RECEIPT: NONE
RUN_RESULT: chat-only
GREEN_WRITER: NO
LEDGER_WRITER: NO
RESULT_WRITER: NO
NEXT_ACTION:
```

## Authority references

- `.cursor/pipeline/run-build-tests/test_run_build_manual.py` (focused helper tests)
- `.cursor/pipeline/contracts/run-build/MANUAL_EXECUTION.md`
- `.cursor/pipeline/contracts/bounded-verify/AUTHORITY_SPLIT.md`
- `.cursor/commands/bounded-executor.md` (durable PASS runner)
- `opsx/project/quality-gates.md` (`poetry-python`; vue-npm frontend build UNAVAILABLE)

## Language

Пояснения — русский. Коды ошибок, пути и `RUN_RESULT` — английский.
