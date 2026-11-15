---
description: "Execute-only explicit pytest nodes (manual) or refuse bounded until S6 wiring."
argument-hint: "mode=manual lane=pytest test_ids=<node-id>[,<node-id>...]"
contract_id: OPSX-WB-RUN-TESTS-01
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

# /opsx-run-tests

SOURCE command name for **atomic execute-only** test runs. TARGET maps frozen
S6 verification PASS to existing `bounded-verify` + `/bounded-executor`; this
command owns **manual explicit pytest node execution** only.

```text
IMPLEMENTATION_STATUS: IMPLEMENTED_UNVERIFIED
RUNTIME_STATUS: ACTIVE_WITH_LIMITS
CONTRACT_ID: OPSX-WB-RUN-TESTS-01
ARCHITECTURAL_ROLE: ATOMIC_EXECUTOR
GREEN_WRITER: NO
TEST_AUTHORING: NO
TRIAGE: NO
LEDGER_WRITER: NO
RESULT_WRITER: NO
SOURCE_PARITY: PARTIAL
```

## Role

Deterministic **execute-only** runner for Owner debug and explicit node recipes.
It never discovers tests, authors tests, or writes durable verification PASS.

| Mode | Authority |
|------|-----------|
| `mode=bounded` | **BLOCKED** — durable PASS remains S6 `bounded_verify_executor` only |
| `mode=manual` `lane=pytest` | Chat-only `RUN_RESULT` via `.cursor/pipeline/run-tests/run_tests.py` |

Preserves SOURCE separation: execution here; `/opsx-test` owns authoring, repair,
and RED/GREEN; `/opsx-test-triage` owns classification.

## User input

```text
$ARGUMENTS
```

| Token | Required | Effect |
|-------|----------|--------|
| `mode=manual` | yes (manual path) | Only supported manual scope token |
| `lane=pytest` | yes (manual path) | Poetry/pytest lane only; other lanes → `CROSS_LANE` |
| `test_ids=<id>[,<id>...]` | yes (manual path) | Exact pytest node ids under `tests/` |
| `mode=bounded` | bounded attempt | Always `BLOCKED` `BINDING_GAP` on this target (no vue-npm resolver) |

### Refused invocation shapes (fail closed)

| Input | STATUS | reason |
|-------|--------|--------|
| `mode` absent or not `manual` / `bounded` | BLOCKED | `MODE_REQUIRED` or `UNKNOWN_MODE` |
| `mode=bounded` (any fields) | BLOCKED | `BINDING_GAP` (`S6_TEST_CAPABILITY_UNREGISTERED`) |
| `lane` not exactly `pytest` | BLOCKED | `CROSS_LANE` |
| Glob, traversal, shell metacharacters in ids | BLOCKED | `GLOB_FORBIDDEN`, `PATH_TRAVERSAL`, `INVALID_TEST_ID` |
| Duplicate ids | BLOCKED | `DUPLICATE_TEST_ID` |
| `@pytest.mark.destructive_local` (or blocking conftest mark) | BLOCKED | `DESTRUCTIVE_LOCAL_BLOCKED` |
| Missing / unknown node | BLOCKED | `MISSING_TEST_ID` or `UNKNOWN_SELECTOR` |
| Runner crash / non-test exit | BLOCKED | `EXECUTION_ERROR` or `INVALID_TEST_ID` |
| Assertion failure | FAIL | `TEST_FAILED` |

## Manual execution helper (sole runner for manual mode)

After gates pass, invoke **only**:

```powershell
poetry run python .cursor\pipeline\run-tests\run_tests.py --repo-root . --arguments "mode=manual lane=pytest test_ids=<id>[,<id>...]"
```

The helper:

- builds shell-free `poetry run pytest <node_id> -vv --tb=short …` argv per id;
- rejects `destructive_local` before spawn (no scoped destructive planner API here);
- emits `RUN_RESULT` text — **not** durable verification evidence.

Do **not** run repository-wide pytest, xdist suite commands, or `/opsx-static-check`
waves from this command.

## Writes

| Path class | Authority |
|------------|-----------|
| `app/**`, `tests/**`, product code | **NONE** |
| `openspec/changes/**/bounded-verify/results/**` | **NONE** (use `/bounded-executor`) |
| `VERIFICATION-LEDGER.json`, plan, bindings (SOURCE carriers) | **NONE** |
| Chat `RUN_RESULT` | manual mode only; `DURABLE_RECEIPT: NONE` |

Manual PASS is **not** archive `GO`, ledger advance, or bounded verification PASS.

## Bounded mode (explicit refusal)

When `$ARGUMENTS` contains `mode=bounded`:

1. Do **not** execute tests.
2. Do **not** write `BOUNDED_CHECK_RESULT` or `results/*.json`.
3. Emit `STATUS: BLOCKED` `reason: BINDING_GAP` with detail
   `S6_TEST_CAPABILITY_UNREGISTERED`.
4. `NEXT_ACTION`: run frozen pytest-family checks through
   `/bounded-executor mode=bounded change=<change_id>` when CURRENT is a pytest
   registry check — not through this command.

SOURCE game `frontend_tests` / `backend_tests` adapters and
`opsx_tools/opsx-run-tests-resolve.mjs` are **not** ported.

## test_id representation (manual pytest v1)

| Form | Example |
|------|---------|
| Module function | `tests/unit/test_foo.py::test_bar` |
| Class method | `tests/unit/test_foo.py::TestFoo::test_bar` |
| Parametrized | `tests/unit/test_foo.py::test_bar[param]` |

Rules (fail closed):

1. Repo-relative path under `tests/` only; reject `..`, absolute paths, `\\`, globs.
2. File name must match `test_*.py`, `*_test.py`, or `tests.py`.
3. Selector must resolve to an existing `test_*` function or `Test*` class method.
4. Multiple ids: run in order; stop on first `BLOCKED`; aggregate `FAIL` if any exit code `1`.

## Failure model (manual helper)

| Condition | STATUS | reason |
|-----------|--------|--------|
| Empty / missing `test_ids` | BLOCKED | `MISSING_TEST_ID` |
| Path outside `tests/` | BLOCKED | `CROSS_LANE` |
| Traversal / glob / injection chars | BLOCKED | `PATH_TRAVERSAL` / `GLOB_FORBIDDEN` / `INVALID_TEST_ID` |
| `destructive_local` selection | BLOCKED | `DESTRUCTIVE_LOCAL_BLOCKED` |
| Pytest collection error (exit 4/5) | BLOCKED | `INVALID_TEST_ID` |
| Spawn / unexpected exit | BLOCKED | `EXECUTION_ERROR` |
| Assertion failure (exit 1) | FAIL | `TEST_FAILED` |
| All exits 0 | PASS | (chat-only) |

## Does NOT

- Author, edit, or repair `tests/**`.
- Run RED/GREEN workflows or write bug evidence.
- Mutate Verification Plan, ledger, bindings, or bounded-verify `results/**`.
- Claim bounded verification PASS without S6 executor `RESULT_RECORDED`.
- Substitute for `/opsx-static-check` FULL/FAST waves.
- Port SOURCE Node `frontend_tests` or unittest `backend_tests` lanes.

## Output format (manual)

```text
CONTRACT_ID: OPSX-WB-RUN-TESTS-01
MODE: manual
STATUS: PASS | FAIL | BLOCKED
reason:
detail:
test_ids:
resolved_commands:
exit_codes:
CACHE_MODE: n/a
DESTRUCTIVE_PLANNER_CALL: NO
DURABLE_RECEIPT: NONE
RUN_RESULT: chat-only
GREEN_WRITER: NO
LEDGER_WRITER: NO
RESULT_WRITER: NO
NEXT_ACTION:
```

`NEXT_ACTION` after manual PASS/FAIL: none required for verification; bounded
work remains `/opsx-next-check` + `/bounded-executor`.

## Authority references

- `.cursor/pipeline/run-tests-tests/test_run_tests_manual.py` (focused helper tests)
- `.cursor/pipeline/contracts/run-tests/MANUAL_EXECUTION.md`
- `.cursor/pipeline/contracts/bounded-verify/AUTHORITY_SPLIT.md`
- `.cursor/commands/bounded-executor.md` (durable PASS runner)
- `.cursor/commands/opsx-test.md` (authoring + destructive planner)
- `.cursor/static-check/contracts/destructive-selective-cache.md`

## Language

Пояснения — русский. Коды ошибок, пути и `RUN_RESULT` — английский.
