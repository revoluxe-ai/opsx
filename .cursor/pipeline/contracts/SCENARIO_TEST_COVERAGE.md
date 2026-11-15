# Scenario Test Coverage Lifecycle

Producer/consumer contract для полного test-evidence lifecycle OpenSpec
Scenarios.

```text
CONTRACT_ID=OPSX_SCENARIO_TEST_COVERAGE_V1
TAXONOMY=.cursor/pipeline/contracts/TEST_EVIDENCE_TAXONOMY.md
```

## Goal

Связать без скрытой истории:

```text
Spec Scenario
→ coverage plan
→ test/refactor packets
→ authored evidence
→ authorized provider execution receipt
→ independent scenario coverage report
→ archive verify
```

Этот contract не даёт writer-команде право выдавать независимый coverage или
archive verdict.

## Authority

```text
/opsx-spec-scenario-coverage
  owns: plan/receipt (mode=plan), gap disposition and independent coverage verdict (audit/certify)
  CLI: mode=plan | mode=audit | mode=certify  (required; no silent default)

/opsx-test
  owns: ContractCase, test fixtures/goldens, fake/stub, scenario tests
  boundary: tests/** only

Planning stage: deterministic plan readiness gate
  entry: opsx/opsx_runtime/run_plan_readiness.py <change>
  owns: read-only plan/receipt/source identity validation
  writes: none; executes after mode=plan materialization, before implementation
  missing/stale input: PAUSE with exact planning-producer handoff

Test-authoring stage: separate LangGraph scenario_trace_materialize
  entry: opsx/opsx_runtime/run_scenario_trace.py <change>
  trigger: /opsx-test completes scenario test authorship (never inside verify)
  owns: deterministic trace mapping of existing authored tests
  writes: openspec/changes/<change>/scenario-coverage/test_trace_matrix.json ONLY
  requires: valid plan + receipt + input identities and real test nodes
  missing/ambiguous mapping: PAUSE, not fabricated PASS
  /opsx-test itself still cannot write openspec/**

Implementation graph + parent (hop agents, full mode):
  route: planning → implementation [apply_design_lane → scenario_test_authoring → slices …] → scenario_trace_materialize → verify
  scenario_test_authoring (inside implementation, after apply-design) owns first-write of canonical `tests/scenarios/<change>/test_<sha>.py`
  scenario_trace_materialize remains the only full writer of `test_trace_matrix.json`
  row-level `rebind_trace_to_strongest_peer` stays verify repair-only

Verify stage: plan_artifacts_preflight (read-only)
  owns: missing/stale planning+trace artifact detection before cached verify skip
  writes: none; NEVER calls the producer
  missing/stale trace: PAUSE with test-authoring handoff
  independent scenario_coverage_verify alone owns behavioral coverage verdict

Verify subgraph row walk (`scenario_coverage_verify` + inline `scenario_test_repair`):
  entry: LangGraph verify after `IMPL_OK` when planning artifacts READY
  owns: построчный check → repair → recheck; per-row green commit; `row_walk_journal.json`
  writes: journal, row observation receipts, optional green commits (not `test_trace_matrix.json`)
  does_not: materialize trace matrix; batch-recompute whole matrix; replace `/opsx-test` authorship
  PAUSE: open repair (`repair=started` without finish), commit fail, `ROW_REPAIR_ATTEMPTS_EXCEEDED`
  repair cap: max 3 **finished** repairs per `scenario_id` across walk resumes (`repair_attempts` in journal); CLI `-r` / `--reset-sm` on verify_impl also zeroes journal `repair_attempts` (checkpoint reset alone does not)
  mapped binding: `scenario_test_repair` MUST edit `frozen_mapped_test_path` / canonical `tests/scenarios/<change>/test_<sha>.py`; peer-only integration/unit edits → `REPAIR_PEER_ONLY_EDIT`
  repair hop: `scenario_test_repair` (LLM/model) edits mapped canonical tests; `reference_test_nodes` in prompt are READ-ONLY assertion templates, not writable peers
  pytest gate: `run_focused_checks` on mapped node runs after `CANDIDATE_PENDING_VERIFY` and before verify recheck (journal `pytest_receipt`)
  matrix rebind: typed `rebind_trace_to_strongest_peer` when `repair_attempts>=2` and mapped body still weak by `mapped_body_is_weak` (journal + `test_trace_matrix.json` update)

/opsx-close-external-gap
  owns: PG/CH production seam, real provider integration, live authorization,
        PROVIDER_EXECUTION_RECEIPT
  does_not_own: final Scenario coverage PASS

/opsx-verify
  owns: archive GO/NO_GO

Owner
  owns: material behavior ambiguity, intentional golden/ContractCase semantic
        change, one-shot live authorization,
        owner-contract.md semantics (via /opsx-owner-contract-review);
        legacy CONFIRM_LIFECYCLE only for Changes without reviewed owner-contract

Planning writers (/opsx-continue, /opsx-ff, /opsx-update)
  own: Derived Lifecycle in design (LIFECYCLE_STATUS: DERIVED) from reviewed
       Owner Contract when PRODUCT_ACCEPTANCE_REQUIRED=true and temporal;
       legacy OWNER_LIFECYCLE_ECHO + CONFIRM only when no reviewed owner-contract
```

## Derived Lifecycle ↔ Spec Scenarios (Product Acceptance)

When `PRODUCT_ACCEPTANCE_REQUIRED=true` (see
`.cursor/pipeline/contracts/PRODUCT_ACCEPTANCE.md`):

**Primary path** (reviewed `owner-contract.md` present):

```text
DERIVED_LIFECYCLE     = durable design.md Lifecycle (prefer) with
                        LIFECYCLE_STATUS: DERIVED + per-step SOURCE
OWNER_CONTRACT_SOURCE = openspec/changes/<change>/owner-contract.md
```

- `/opsx-spec-scenario-coverage` **consumes** Derived Lifecycle (+ Owner
  Contract for semantic verification); it does not author OpenSpec artifacts
  and does not re-ask Owner for product meaning.
- Prefer primary planning source: `design.md` Lifecycle. Fall back to
  deriving the same sequence from owner-contract + decisions + specs when
  design omits an explicit section — if derivation would invent semantics →
  `PLANNING_LIFECYCLE_GAP` or `UNRESOLVED_PRODUCT_SEMANTICS` (do not invent).
- `mode=plan` with Derived Lifecycle (or unambiguously derivable equivalent)
  → build `SCENARIO_COVERAGE_PLAN` / `LIFECYCLE_COVERAGE` matrix
  (L* ↔ Spec Scenarios). Do **NOT** emit `BLOCKED_NEEDS_OWNER_LIFECYCLE` or
  require `CONFIRM_LIFECYCLE`.
- Missing Spec Scenario for a known L* → `SPEC_SCENARIO_MISSING` /
  `SPEC_COVERAGE_GAP` (NEXT: `/opsx-continue` | `/opsx-ff` | `/opsx-update`
  planning writer) — **not** `NEEDS_OWNER`.
- Design omits a transition proven by Owner Contract →
  `PLANNING_LIFECYCLE_GAP` (repair planning; do not re-quiz Owner).
- Spec invents behavior absent from lifecycle/Owner Contract →
  `SEMANTIC_DRIFT` (not PASS).
- Genuine unresolved product fork → `UNRESOLVED_PRODUCT_SEMANTICS` →
  `/opsx-owner-contract-review` (not `CONFIRM_LIFECYCLE`).
- `mode=audit` / `mode=certify`: acceptance-relevant rows MUST map to
  Derived Lifecycle steps. Unmapped → FAIL/BLOCKED for that scenario.

**Legacy compatibility** (Changes **without** reviewed owner-contract):

```text
OWNER_LIFECYCLE_ECHO   = durable temporal steps in design/decision/spec
CONFIRM_LIFECYCLE      = Owner token after echo is written
```

- `mode=plan` without durable echo on this legacy path only →
  `BLOCKED_NEEDS_OWNER_LIFECYCLE` (NEXT: `/opsx-update` or `/opsx-ff`).
- Scenario report `VERDICT=PASS` **does not** clear PA LIFECYCLE if
  Derived Lifecycle (primary) or legacy echo/CONFIRM (legacy) is missing
  when required.

Example plan-oriented output shape:

```text
LIFECYCLE_COVERAGE
L1 … → COVERED: S-01
L2 … → COVERED: S-02
L6 … → PARTIAL / SPEC_SCENARIO_MISSING: …
```

## CLI modes (binding)

Command surface for `/opsx-spec-scenario-coverage` MUST require:

```text
mode=plan | mode=audit | mode=certify
change=<change-id>
```

| mode | Produces | Must not |
|---|---|---|
| `plan` | `SCENARIO_COVERAGE_PLAN` | claim tests exist; emit coverage PASS; execute tests |
| `audit` | `SCENARIO_TEST_COVERAGE_REPORT` + gaps/packets; `INDEPENDENT_REAUDIT` when a required PG/CH receipt is still missing or stale | silent-default when `mode=` missing; full PASS via `scenario=` focus; contract full PASS while a required PG/CH receipt is missing or stale; `CERTIFY_PACKET: none` in that case |
| `certify` | full-change report; PASS only with fresh `PROVIDER_EXECUTION_RECEIPT` when PG/CH required | PASS without receipts; accept `scenario=` focus as full certify |

Fail-closed arg rules (missing `mode=`, unknown `mode=`, foreign tokens such as
`base=` / `AUTHORIZE_ONE_LIVE_RUN` / writer `GO`, invalid combos) are owned by
the command file Arg gate. Contract consumers and handoffs MUST pass explicit
`mode=`; do not imply default `audit`.

Recommended handoffs:

```text
/opsx-spec-scenario-coverage mode=plan change=<id>
/opsx-spec-scenario-coverage mode=audit change=<id>
/opsx-spec-scenario-coverage mode=certify change=<id>
```

## Lifecycle states

```text
coverage_unplanned
coverage_plan_ready
coverage_gaps
coverage_tests_authored
coverage_provider_ready
coverage_live_blocked
coverage_receipt_ready
coverage_certified
```

Recommended transition:

```text
planning artifacts ready
→ scenario coverage plan
→ apply
→ scenario coverage audit
→ test-only and/or refactor writers
→ independent re-audit
→ authorized live provider execution when required
→ scenario coverage certification
→ verify
```

## Stable identity

```text
SCENARIO_ID=<repo-relative-delta-spec-path>::<exact Scenario heading>
GAP_ID=<change-id>::<scenario-id-hash>::<ordinal>
GAP_FILE_ID=<first-16-hex of sha256(GAP_ID)>
CONTRACT_ID=<stable boundary + case-set identity>
```

Не переименовывай Scenario только ради test mapping. Изменение exact Scenario
heading инвалидирует прежнюю привязку и требует нового plan/audit.
`GAP_ID` хранится только как data; для Windows-safe filenames используй только
`GAP_FILE_ID` (`[0-9a-f]{16}`), без `:`, `/`, `\` или Scenario title.

## Steady-state / already-true reconcile (mandatory)

When Spec, design, or an accepted decision implies an invariant that must hold
on **observe / reconcile / deploy** (not only after a producer event), the
change MUST include at least one Spec Scenario shaped as:

```text
GIVEN already-true precondition state (no fresh producer event required)
WHEN periodic / ensure / reconcile entrypoint runs
THEN the same allowed effect as the post-event transition path
     (+ paired forbidden sibling if the Scenario is a gate)
```

Encoding **only** post-event transition producers for that invariant is
**FAIL_CLOSED**:

| Where | Required outcome |
|---|---|
| `mode=plan` | `OPEN_QUESTIONS` (or `PLAN_STATUS=provisional\|blocked`) naming the missing steady-state Scenario |
| `mode=audit` / `mode=certify` | gap class `MISSING_STEADY_STATE_RECONCILE_SCENARIO` → row/report FAIL |

Disposition:

```text
Spec omits the steady-state Scenario
→ PLANNING_UPDATE → /opsx-update

Spec has the Scenario but evidence is missing/weak
→ TEST_ONLY → /opsx-test
```

Non-normative class note (do not treat as product requirement text here):
ROOT-001 / empty-keywords — empty active keywords + Search STARTED + bot on
must arm Search EMPTY_KEYWORDS pause without a prior V3 intent (activity-control
reconcile), not only after a FORCE_OFF → intent producer hop.

## Common freshness envelope

Каждый packet/report/receipt MUST содержать применимые поля:

```text
SCHEMA_VERSION: 1
CHANGE_ID:
GAP_ID:
GAP_FILE_ID:
SOURCE_SPEC_SHA:
WORKTREE_FINGERPRINT:
CONTRACT_ID:
CONTRACT_SHA:
TEST_CONTENT_SHA:
PRODUCTION_BOUNDARY_SHA:
FAKE_SHA:
SCHEMA_SHA:
CREATED_AT:
PRODUCER_COMMAND:
```

Неприменимое поле записывай `not_applicable`, а неизвестное —
`unknown` с причиной. Не подставляй пустую строку.

Изменение обязательного content hash делает downstream evidence `STALE`.

`WORKTREE_FINGERPRINT`, когда он равен `HEAD`, и `PROVENANCE_HEAD` — происхождение прогона. Для `PROVIDER_EXECUTION_RECEIPT` они не являются freshness gate. Свежесть PASS — это `INPUT_IDENTITY` (см. ниже). Несовпадение одного только HEAD после коммита receipt не делает PASS stale.

### Evidence input identity

`CONTRACT_ID: OPSX_EVIDENCE_INPUT_IDENTITY_V0`

Оракул: `.cursor/pipeline/evidence-input-identity/evidence_input_identity.py`

`INPUT_IDENTITY` — SHA-256 сырых байт точного набора входов сертифицируемого изменения. Роли, каждая непустая:

- `product` — продуктовый код, от которого зависит изменение
- `tests` — тесты
- `specs` — спецификации
- `validators` — валидаторы, включая файл оракула
- `configs` — конфиги, от которых зависит изменение

Receipt в набор не входит: путь `opsx/runs/**/receipts/**` и явный `RECEIPT_PATH` пропускаются при обходе и отклоняются, если роль перечисляет их как файл. Коммит или добавление только этого receipt MUST NOT менять `INPUT_IDENTITY` и MUST NOT делать предыдущий PASS stale.

Любое изменение байт любой материальной роли MUST менять `INPUT_IDENTITY` и MUST делать предыдущий PASS stale.

`FRESH` допустим только при `RESULT=PASS` и равенстве пересчитанного `INPUT_IDENTITY` записанному. Иной `RESULT`, пустая роль и отсутствие `INPUT_IDENTITY` — не fresh.

Предзапусковое равенство `AUDITED_HEAD` в `/opsx-close-external-gap` остаётся launch-guard и не заменяется этой идентичностью.

## SCENARIO_COVERAGE_PLAN

Plan описывает required evidence, но не утверждает, что оно существует.

```text
SCENARIO_COVERAGE_PLAN:
  SCHEMA_VERSION: 1
  CHANGE_ID:
  SOURCE_SPEC_SHA:
  PLAN_STATUS: ready | provisional | blocked
  SCENARIOS:
    - SCENARIO_ID:
      GIVEN_WHEN_THEN:
      PRODUCTION_ENTRYPOINT:
      CAUSAL_PATH:
      THIN_ENDS:
      REQUIRED_EVIDENCE:
        SCENARIO_SEAM:
        CONTRACT_CASES:
        STUB_CONTRACT:
        BOUNDARY_INTEGRATION_WRITE:
        BOUNDARY_INTEGRATION_READ:
        BOUNDARY_INTEGRATION_OTHER:
        LOAD_OR_PERFORMANCE:
      REALITY_CONSTRAINTS:
      MOCKING_CONSTRAINTS:
  OPEN_QUESTIONS:
```

Material behavior ambiguity → `/opsx-update`, не догадка planner-а.

## DURABLE_PLAN_EVIDENCE (`mode=plan` only)

When scenario coverage planning is a required gate input for a later
independent consumer (`/opsx-continue` / `/opsx-ff` task generation,
`/opsx-planning-review` P6, Product Acceptance lifecycle coverage), the
plan MUST be materialized as durable, machine-checkable Change evidence.
Chat output alone is **not** SoT.

### Canonical paths

```text
openspec/changes/<CHANGE_ID>/scenario-coverage/plan.md
openspec/changes/<CHANGE_ID>/scenario-coverage/scenarios/<sha256(scenario_id)>.md
openspec/changes/<CHANGE_ID>/scenario-coverage/plan.receipt.json
```

`plan.md` хранит change-level разделы и не содержит тел `- SCENARIO_ID:`.
Один `scenario_id` — один файл в `scenarios/`. Тела в `plan.md` — PAUSE
(`COVERAGE_PLAN_EMBEDDED`), даже если каталог `scenarios/` уже есть.

One logical evidence pair. Do **not** use `.tmp` as the canonical
cross-command SoT for plan evidence (`opsx/runs/scenario-coverage/` remains
for provider/audit receipts only).

### Canonical scenario tests

```text
tests/scenarios/<CHANGE_ID>/test_<sha256(scenario_id)>.py
```

Runtime SoT: `opsx/opsx_runtime/agents/common/scenario_files.py`
(`scenario_test_relpath`).

- Exactly **one** mapped `test_*` function per file; Russian scenario
  docstring with `SCENARIO_ID` and plan markers (`Смысл`, `Исходные условия`,
  `Действие`, `Ожидаемый результат`).
- `test_trace_matrix.json` `test_node_id` MUST reference this path (not a
  shared integration module hosting multiple scenarios).
- Shared fixtures/helpers (`conftest.py`, `*_helpers.py`, integration
  packages) MAY live outside `tests/scenarios/<CHANGE_ID>/`; row reuse and
  independent audit bind only the **canonical mapped module** bytes, not
  helper paths.
- `/opsx-test` in scenario context MUST author or update tests only at the
  canonical path; MUST NOT add another scenario to an existing multi-scenario
  `test_*.py` under `tests/integration/` or elsewhere.

Verify preflight and `run_scenario_plan_migrate.py --check` enforce layout
(`require_canonical_tests`); violations → `SCENARIO_TEST_*` / handoff to
`/opsx-test` + trace materialization.

### Sole writer

```text
SOLE_WRITER: /opsx-spec-scenario-coverage mode=plan
```

`/opsx-continue`, `/opsx-ff`, `/opsx-update`, `/opsx-planning-review` MUST
NOT create or repair plan evidence. They only read, validate, consume, or
request rerun.

### Human plan (`plan.md`)

Reviewable content MUST include at least:

```text
CHANGE_ID
MODE: plan
PLAN_STATUS: ready | provisional | blocked
PLAN_EVIDENCE_ID
LIFECYCLE_STATUS
LIFECYCLE_COVERAGE   # L* ↔ Spec Scenario mapping (or gaps)
SCENARIO_COVERAGE_PLAN  # matrix / required evidence (schema above)
UNRESOLVED_GAPS | OPEN_QUESTIONS
GENERATED_FROM         # authoritative input identities/hashes
```

Plan is **DERIVED PLANNING EVIDENCE** only. It MUST NOT invent product
semantics or become Owner/design/spec authority.

### Machine receipt (`plan.receipt.json`)

```text
SCHEMA_VERSION: 1
SCENARIO_COVERAGE_RECEIPT: true
CHANGE_ID:
MODE: plan
PLAN_STATUS: ready | provisional | blocked
PLAN_EVIDENCE_ID:
PLAN_CONTENT_HASH:        # sha256 of remaining plan.md plus canonical (scenario_id, file_sha256) list
SCENARIO_FILES:           # [{scenario_id, path, content_hash}] in plan order
PLAN_PATH:
RECEIPT_PATH:
SCENARIO_COUNT:
LIFECYCLE_COVERAGE:
  covered: []
  gaps: []
UNRESOLVED_GAPS: []
INPUTS:
  DESIGN_REVISION: { path, content_hash }
  SPEC_REVISION: [ { path, content_hash }, ... ]
  LIFECYCLE_REVISION: { source, content_hash }
  OWNER_CONTRACT_REVISION: { path, content_hash } | omitted
GENERATED_FROM:           # same hashes as INPUTS (deterministic identity)
MATERIALIZATION_STATUS: COMMITTED
```

Optional timestamps MUST NOT enter `PLAN_EVIDENCE_ID` / `PLAN_CONTENT_HASH`.

`content_hash` = sha256 of LF-normalized file bytes at write time. v1 conservatism:
`DESIGN_REVISION` / `LIFECYCLE_REVISION` MAY both hash whole `design.md`
(any design change → STALE is acceptable). Freshness checks MUST use
``CheckWorkflowReceipt.matches_file`` (or the agent's injected
``CheckWorkflowReceipt`` with pinned INPUTS): CRLF vs LF and benign
whitespace MUST NOT alone mark INPUTS stale; semantic text changes MUST.

### Freshness / integrity

Consumers MUST classify evidence as exactly one of:

```text
MISSING | CURRENT | STALE | INTEGRITY_FAIL
```

- missing plan or receipt → `MISSING`
- any `INPUTS.*` fails agent ``CheckWorkflowReceipt`` pin match → `STALE`
- `PLAN_CONTENT_HASH` ≠ hash(remaining `plan.md` + canonical scenario file list) → `INTEGRITY_FAIL`
- `- SCENARIO_ID:` still present in `plan.md` → PAUSE (`COVERAGE_PLAN_EMBEDDED`), catalog or not
- otherwise → `CURRENT`

`PLAN_READY(design=A, spec=B)` MUST NOT silently remain CURRENT after
design=A2 or covered-spec=B2.

### Atomicity

```text
compute full result → validate → write plan.md → write receipt
```

Partial materialization MUST NOT yield durable `ready`. On write failure:

```text
MATERIALIZATION_STATUS: FAILED
PLAN_STATUS: NOT_COMMITTED   # chat may report analysis, not durable ready
```

Recovery: rerun `mode=plan`. Do not leave two ambiguous CURRENT pairs;
rerun replaces/supersedes the canonical pair.

### Idempotency

Identical authoritative inputs + same deterministic plan semantics →
same `PLAN_EVIDENCE_ID` / `PLAN_CONTENT_HASH`. Second run MAY report
`MATERIALIZATION_STATUS: ALREADY_CURRENT` without rewrite.

### Result envelope (chat + durable)

After materialization attempt, producer returns:

```text
SCENARIO_COVERAGE_PLAN_RESULT
CHANGE_ID:
MODE: plan
PLAN_STATUS: ready | provisional | blocked | NOT_COMMITTED
MATERIALIZATION_STATUS: COMMITTED | ALREADY_CURRENT | FAILED
PLAN_EVIDENCE_ID:
PLAN_PATH:
RECEIPT_PATH:
INPUT_FRESHNESS: current | stale | missing | n/a
UNRESOLVED_GAPS:
```

Only `COMMITTED` / `ALREADY_CURRENT` may be treated as durable gate input.

### Mode isolation

```text
PLAN evidence ≠ audit report evidence ≠ certify / provider evidence
```

This section authorizes durable writes for `mode=plan` evidence paths only.

## Gap dispositions

```text
TEST_ONLY
  tests/** достаточно; production seam уже пригоден

REFACTOR_AND_TEST
  требуется PG/CH production seam/session-client handoff + integration

CERTIFY
  code/tests authored; не хватает свежего authorized provider execution

PLANNING_UPDATE
  Spec/target/behavior неоднозначны
  (includes MISSING_STEADY_STATE_RECONCILE_SCENARIO when Spec omits
   already-true / reconcile Scenario for a reconcile-on-observe invariant)
```

## SCENARIO_TEST_ONLY_PACKET

Consumer: `/opsx-test`.

```text
SCENARIO_TEST_ONLY_PACKET:
  SCHEMA_VERSION: 1
  SOURCE: SCENARIO_TEST_COVERAGE_REPORT
  CHANGE_ID:
  SOURCE_SPEC_SHA:
  WORKTREE_FINGERPRINT:
  ITEMS:
    - GAP_ID:
      GAP_FILE_ID:
      SCENARIO_ID:
      DISPOSITION: TEST_ONLY
      REQUIRED_ARTIFACTS:
        CONTRACT_CASES:
        FAKE_OR_STUB:
        SCENARIO_SEAM_TEST:
        STUB_CONTRACT_TEST:
        BOUNDARY_ADAPTER_TEST:
      PRODUCTION_ENTRYPOINT:
      REAL_BOUNDARY_SYMBOLS:
      REALITY_CONSTRAINTS:
      MOCKING_CONSTRAINTS:
```

Writer MUST вернуть per-item test ids и hashes. Он не выдаёт independent
coverage PASS.

## SCENARIO_REFACTOR_AND_TEST_PACKET

Consumer: `/opsx-close-external-gap`; только PG/CH и доказанный
`UNTESTABLE_SEAM`.

```text
SCENARIO_REFACTOR_AND_TEST_PACKET:
  SCHEMA_VERSION: 1
  SOURCE: SCENARIO_TEST_COVERAGE_REPORT
  CHANGE_ID:
  SOURCE_SPEC_SHA:
  WORKTREE_FINGERPRINT:
  ITEMS:
    - GAP_ID:
      GAP_FILE_ID:
      SCENARIO_ID:
      DISPOSITION: REFACTOR_AND_TEST
      KIND: PG | CH
      PRODUCTION_BOUNDARY:
      REQUIRED_SEAM:
      CONTRACT_ID:
      CONTRACT_SHA:
      CONTRACT_CASES_PATH:
      REQUIRED_PROVIDER_TESTS:
        WRITE:
        READ:
        READ_AFTER_WRITE:
      SESSION_MODE_INTENT:
```

Close writer MUST NOT менять reviewed ContractCase semantic expectation.
Mismatch → `BLOCKED_CONTRACT_MISMATCH` или Owner semantic decision.

## SCENARIO_CERTIFY_PACKET

Consumer: `/opsx-close-external-gap`; v1 только PG/CH live/destructive
provider execution.

```text
SCENARIO_CERTIFY_PACKET:
  SCHEMA_VERSION: 1
  SOURCE: SCENARIO_TEST_COVERAGE_REPORT
  CHANGE_ID:
  WORKTREE_FINGERPRINT:
  ITEMS:
    - GAP_ID:
      GAP_FILE_ID:
      SCENARIO_ID:
      DISPOSITION: CERTIFY
      KIND: PG | CH
      CONTRACT_ID:
      CONTRACT_SHA:
      PROVIDER_TEST_IDS:
      TEST_CONTENT_SHA:
      PRODUCTION_BOUNDARY_SHA:
      SCHEMA_SHA:
      REQUIRED_EXECUTION_CLASS: destructive_local | authorized_live
```

`AUTHORIZE_ONE_LIVE_RUN` остаётся one-shot и принимается только по собственному
contract `/opsx-close-external-gap`. `/opsx-spec-scenario-coverage` его не
принимает и receipt не пишет.

### Content hashes (v1)

Auditor пересчитывает hashes из байт текущего worktree. Не копировать их из
чата и не оставлять placeholder.

```text
single-file SHA = lowercase hex SHA-256 of the file's raw bytes
  (CONTRACT_SHA, SCHEMA_SHA when each names one file)
multi-file SHA = lowercase hex SHA-256 of the concatenation, in listed order:
  for each file: UTF-8 path, NUL, LF-normalized file bytes, NUL
  (TEST_CONTENT_SHA, PRODUCTION_BOUNDARY_SHA)
GAP_FILE_ID = first 16 hex chars of SHA-256(GAP_ID)  # Stable identity
```

Рядом с hash публикуй список файлов и recipe, чтобы receipt можно было сверить.
Audited-state hashes (`AUDITED_STATUS_SHA256`, `AUDITED_TRACKED_DIFF_SHA256`,
`AUDITED_UNTRACKED_MANIFEST_SHA256`) и `ALLOWED_RUN_BLOCK_SHA256` считаются
каноническими командами из `/opsx-close-external-gap` (B2).
`ALLOWED_RUN_BLOCK_SHA256` — SHA-256 точных байт frozen shell block,
опубликованного в том же audit output (env validation, exact pytest
invocation, teardown — в этом порядке).

## Independent provider re-audit

До live authorization независимый
`/opsx-spec-scenario-coverage mode=audit change=<CHANGE_ID>`
должен проверить authored provider tests и current audited state:

```text
INDEPENDENT_REAUDIT:
  SCHEMA_VERSION: 1
  SOURCE_COMMAND: /opsx-spec-scenario-coverage
  VERDICT: READY_FOR_ONE_AUTHORIZED_RUN
  CHANGE_ID:
  GAP_IDS:
  GAP_FILE_IDS:
  CONTRACT_IDS:
  CONTRACT_SHAS:
  PROVIDER_TEST_IDS:
  TEST_CONTENT_SHAS:
  PRODUCTION_BOUNDARY_SHAS:
  SCHEMA_SHAS:
  AUDITED_HEAD:
  AUDITED_STATUS_SHA256:
  AUDITED_TRACKED_DIFF_SHA256:
  AUDITED_UNTRACKED_MANIFEST_SHA256:
  ALLOWED_RUN_BLOCK_SHA256:
  CREATED_AT:
```

Любое изменение audited state после re-audit инвалидирует live readiness.
Writer `/opsx-close-external-gap` MUST NOT выпускать этот verdict сам.

## PROVIDER_EXECUTION_RECEIPT

Producer: `/opsx-close-external-gap` после фактического разрешённого запуска.

Recommended durable path:

```text
opsx/runs/scenario-coverage/<CHANGE_ID>/receipts/PROVIDER-<GAP_FILE_ID>.md
```

Schema:

```text
PROVIDER_EXECUTION_RECEIPT:
  SCHEMA_VERSION: 1
  CHANGE_ID:
  GAP_ID:
  GAP_FILE_ID:
  SCENARIO_ID:
  KIND: PG | CH
  CONTRACT_ID:
  CONTRACT_SHA:
  PROVIDER_TEST_IDS:
  TEST_CONTENT_SHA:
  PRODUCTION_BOUNDARY_SHA:
  SCHEMA_SHA:
  INPUT_IDENTITY:
  PROVENANCE_HEAD:
  RECEIPT_PATH:
  INPUT_ROOT product: <relative root or file>
  INPUT_ROOT tests: <relative root or file>
  INPUT_ROOT specs: <relative root or file>
  INPUT_ROOT validators: <relative root or file>
  INPUT_ROOT configs: <relative root or file>
  ROLE_DIGEST product: <sha256>
  ROLE_DIGEST tests: <sha256>
  ROLE_DIGEST specs: <sha256>
  ROLE_DIGEST validators: <sha256>
  ROLE_DIGEST configs: <sha256>
  WORKTREE_FINGERPRINT:
  EXECUTED_AT:
  EXECUTION_CLASS:
  RESULT: PASS | FAIL | ERROR | BLOCKED
  TESTS_PASSED:
  TESTS_FAILED:
  AUTHORIZATION_STATUS: consumed | not_required
  LOG_PATH:
  LOG_SHA:
  SECRETS_REDACTED: YES
```

Receipt:

- не содержит DSN, credentials или raw production/customer data;
- не является Scenario coverage PASS;
- stale при несовпадении `INPUT_IDENTITY` (любая материальная роль);
- не stale от добавления или коммита самого receipt и от сдвига одного только HEAD;
- `PROVENANCE_HEAD` / `WORKTREE_FINGERPRINT` записывают HEAD и не заменяют `INPUT_IDENTITY`;
- `INPUT_ROOT` (все пять ролей), `ROLE_DIGEST` и `RECEIPT_PATH` обязательны для пересчёта. Поля можно писать с отступом, как в schema выше; оракул принимает и нулевой столбец, и отступ. `RECEIPT_PATH` MUST быть receipt-артефактом (`opsx/runs/**/receipts/**`). Материальный файл в этом поле — fail closed, не исключение из дайджеста;
- не создаётся для неисполненного/collected-only теста.

## SCENARIO_TEST_COVERAGE_REPORT

Producer: независимый `/opsx-spec-scenario-coverage`.

```text
SCENARIO_TEST_COVERAGE_REPORT:
  SCHEMA_VERSION: 1
  CHANGE_ID:
  SOURCE_SPEC_SHA:
  WORKTREE_FINGERPRINT:
  ACTIVATION:
    SCENARIO_COVERAGE_REQUIRED: true | false
    PRODUCT_ACCEPTANCE_REQUIRED: true | false | unknown
    ACTIVATION_VERSION:
  VERDICT: PASS | FAIL | BLOCKED
  SCENARIOS_TOTAL:
  SCENARIOS_PASS:
  SCENARIOS_FAIL:
  SCENARIOS_BLOCKED:
  ROWS:
    - SCENARIO_ID:
      GAP_IDS:
      GAP_FILE_IDS:
      REQUIRED_TEST_IDS:
      EXECUTED_TEST_IDS:
      STUB_CONTRACT_STATUS:
      PROVIDER_RECEIPTS:
      FRESHNESS:
      ROW_VERDICT:
      GAPS:
  ROUTE_PACKETS:
    TEST_ONLY_PACKET:   # full SCENARIO_TEST_ONLY_PACKET when any TEST_ONLY gap
    REFACTOR_AND_TEST_PACKET:
    CERTIFY_PACKET:
```

На `VERDICT=FAIL` с ≥1 gap disposition `TEST_ONLY`:
`ROUTE_PACKETS.TEST_ONLY_PACKET` MUST быть полным pasteable
`SCENARIO_TEST_ONLY_PACKET` (не пустой stub, не «см. GAPS»). Каждый blocking
stub thin end → отдельный `ITEMS[]` (или явные `REQUIRED_ARTIFACTS` на item)
с `CONTRACT_CASES` / `FAKE_OR_STUB` / `STUB_CONTRACT_TEST` /
`BOUNDARY_ADAPTER_TEST` где применимо. FAIL без такого packet = неполный
report (невалидный writer handoff).

Full PASS требует:

```text
all Spec Scenarios audited
all required scenario tests executed green
all used stubs have executable contract parity
machine regression oracle OPSX_SCENARIO_AUDIT_REGRESSION_V0
  (.cursor/pipeline/scenario-audit-regression/) MUST fail closed when a
  regression fixture records green pytest but stub_observed_call diverges
  from shared_contract_source or boundary_observed_call (deep HTTP JSON);
  oracle PASS is STUB_CONTRACT_REGRESSION_PASS with
  PASS_SCOPE=REGRESSION_FIXTURE_ONLY only
all required provider receipts present, PASS and fresh
no weak/missing/blocked row
no MISSING_STEADY_STATE_RECONCILE_SCENARIO when reconcile-on-observe applies
```

### Green rows, required provider receipt still missing

Когда `mode=audit` видит, что каждый Spec Scenario row иначе зелёный
(seams authored, stub parity есть, нет TEST_ONLY / planning gap), при этом
PostgreSQL или ClickHouse end обязателен (design lifecycle / store barrier)
и нет fresh `PROVIDER_EXECUTION_RECEIPT`, у которого `INPUT_IDENTITY` совпадает с пересчётом оракула (не равенство HEAD):

```text
не выдавать contract full PASS (VERDICT не PASS)
STATUS: BLOCKED
REASON: NEEDS_PROVIDER_RECEIPT
ROUTE_PACKETS.CERTIFY_PACKET = непустой SCENARIO_CERTIFY_PACKET
  (реальные hashes audited files; не none; не placeholder)
INDEPENDENT_REAUDIT.VERDICT = READY_FOR_ONE_AUTHORIZED_RUN
  (включая GAP_FILE_IDS, audited-state hashes, ALLOWED_RUN_BLOCK_SHA256)
NEXT_ACTION = /opsx-close-external-gap
```

Один `ITEMS[]` на distinct provider suite (`KIND: PG|CH`). `SCENARIO_ID` —
exact scenario, чей lifecycle step является этим store barrier.
`PROVIDER_TEST_IDS` — полный список тестов этого suite.
`mode=certify` остаётся receipt-only: без fresh receipt это
`BLOCKED` / `NEEDS_PROVIDER_RECEIPT`, и certify receipt не авторствует.

## Verify activation

Product Acceptance (kernel DONE) — see
`.cursor/pipeline/contracts/PRODUCT_ACCEPTANCE.md`:

```text
PRODUCT_ACCEPTANCE_REQUIRED=true
→ LIFECYCLE_COVERAGE requires durable Derived Lifecycle
  (LIFECYCLE_STATUS: DERIVED; prefer design.md) from reviewed Owner Contract
  OR legacy OWNER_LIFECYCLE_ECHO + CONFIRM_LIFECYCLE when no reviewed
  owner-contract
→ AND fresh report VERDICT=PASS with rows mapped to lifecycle steps
→ missing Derived Lifecycle (primary) / missing legacy echo+CONFIRM (legacy) /
  UNRESOLVED product forks / unmapped rows / stale/FAIL/BLOCKED report
  is archive blocker
→ do NOT fail solely for absent CONFIRM_LIFECYCLE when Derived Lifecycle
  is present under reviewed Owner Contract
→ legacy SCENARIO_COVERAGE_REQUIRED absent|false does NOT waive this pillar
→ scenario PASS alone never creates DONE without Derived Lifecycle (or
  legacy confirmed echo)
```

Standalone scenario flag (still honored):

```text
SCENARIO_COVERAGE_REQUIRED=true
→ fresh SCENARIO_TEST_COVERAGE_REPORT VERDICT=PASS required
→ missing/stale/FAIL/BLOCKED is archive blocker
```

Legacy compatibility **only when Product Acceptance is NOT required**:

```text
PRODUCT_ACCEPTANCE_REQUIRED=false|NOT_REQUIRED
AND SCENARIO_COVERAGE_REQUIRED absent|false
→ report absence is not automatic archive NO_GO
```

Verify MUST NOT recompute the whole scenario matrix. It validates report
identity/freshness and spot-checks acceptance-relevant evidence (including
SIDE_EFFECT_PROOF rows when PA required). Построчный row walk likewise MUST
NOT call `run_scenario_trace.py` or rewrite `test_trace_matrix.json`; repair
MAY update a matrix **row** via `_bind_matrix`, trace producer remains
`run_scenario_trace.py` after authored tests exist.

## Canonical scenario test authorship (`/opsx-test`)

When `SCENARIO_ID` is known (packet or free-form):

```text
tests/scenarios/<change-id>/test_<sha256(scenario_id)>.py
```

- one mapped `test_*` function per scenario (parametrization inside allowed);
- Russian docstring with full `SCENARIO_ID`;
- matrix `test_node_id` points at that function.

Do **not** rely on arbitrary legacy modules for new scenario seams. See
`.cursor/commands/opsx-test.md` → **Authorship responsibility**.

## Legacy layout migrator (operator only)

CLI: `opsx/opsx_runtime/run_scenario_plan_migrate.py`

| Flag | Effect |
|------|--------|
| `--write` | scenario bodies → `scenarios/<sha>.md` |
| `--write-tests` | move **existing** mapped test module per **current** matrix entries |
| `--check` | strict layout validation (after transfer when using `--write-tests`) |

**Not** in the normal `/opsx-test` → trace → audit chain: on first authorship
`test_trace_matrix.json` may not exist yet; `--write-tests` requires matrix
entries.

**Manual scope:** default traversal can touch **all** `changes` and `archive`;
operator runs with explicit repo/change understanding — not an automatic step
inside work on one change.

## Preflight vs row repair (verify)

Aligned with `planning_artifacts.py` and row walk:

| Situation | Preflight / verify gate | Next owner |
|-----------|-------------------------|------------|
| Matrix row exists, `test_node_id` **empty** | READY (not «Empty test_node_id» hard block) | focused audit → GAP → row repair |
| Matrix row exists, node set, **test file missing** | READY (not `TRACE_TEST_MISSING` for row) | row repair / `/opsx-test` authorship |
| **No** `test_trace_matrix.json` | `TRACE_MATRIX_MISSING` (BLOCKED) | `/opsx-test` → `run_scenario_trace.py` |
| Plan row missing from matrix vs frozen plan | `TRACE_MATRIX_ROW_MISMATCH` | trace producer / planning, not row repair alone |
| Matrix stale (`plan_evidence_id`) | `TRACE_MATRIX_STALE` | `run_scenario_trace.py` |

**Roles (unchanged):**

- **Auditor** (`scenario_coverage_verify`) — read-only coverage verdict per row.
- **Row / scenario test repair** — writes canonical test; may add/update matrix row.
- **Trace materializer** (`run_scenario_trace.py`) — sole writer of full
  `test_trace_matrix.json` from existing test nodes.

**Missing trace handoffs:** «missing trace → PAUSE» is **not** one blanket rule.
No matrix file / row mismatch / stale plan id → planning or test-authoring
handoff. Empty node or missing test file on an **existing** row → row repair
inside verify walk, not necessarily `TRACE_MATRIX_MISSING`.

## Anti-loop rules

- Auditor never writes tests/production.
- Writer never self-issues independent coverage PASS.
- Provider live output never auto-updates ContractCase/golden.
- Re-audit uses current hashes, not chat memory.
- FAIL packet routes to exactly one owning writer per item.
- Receipt is provider evidence, not final certification.
- Green scenario rows with a required PG/CH end and no fresh receipt, without
  a pasteable `SCENARIO_CERTIFY_PACKET` plus `INDEPENDENT_REAUDIT`
  `READY_FOR_ONE_AUTHORIZED_RUN`, are an incomplete auditor handoff — do not
  send Owner to `mode=certify` or claim contract full PASS.
- FAIL with TEST_ONLY gaps without pasteable `SCENARIO_TEST_ONLY_PACKET` in
  `ROUTE_PACKETS` is an incomplete auditor handoff — do not send Owner to
  free-form `/opsx-test` with prose gaps alone.
- Writer closing a TEST_ONLY batch MUST inventory stub thin ends used by failing
  seams; partial closure (e.g. WB ContractCase only while notify stub remains)
  without explicit `REMAINING_GAPS` is forbidden.
- Scenario row walk: only **SUPPORTED** closes a row. GAP / CONTRACT_CONFLICT / any
  non-SUPPORTED → repair immediately (GAP at the same row digest is **not** reused).
  Row skip reuse: durable per-row **PASS receipt** on v3 `row_digest` via
  `resolve_row_work(..., disposition_mode="walk")`; journal `recheck` is not proof.
  **Evidence migration (canonical layout):** after ``--write-tests`` / v3 digest
  changes without product re-audit, run
  ``poetry run python opsx/opsx_runtime/run_migrate_scenario_evidence.py --write``
  to rewrite receipts and journal tokens from prior SUPPORTED rows; it does not
  weaken ``review_proof_error``.
  Repair lease / `repair_attempts` stay in orchestrator, not in resolve. Per row:
  audit → if not SUPPORTED and `repair_attempts < 3` →
  repair → recheck → repeat until SUPPORTED or `repair_attempts` reaches **3**;
  then `ROW_REPAIR_ATTEMPTS_EXCEEDED` PAUSE and Owner Attention. At most **3**
  completed repairs per `scenario_id` (journal `repair_attempts`); aborted
  `INTERRUPTED` does not count.
  **Repair lease in journal:** `mark_repair_started` records `repair_run_uuid` and
  `repair_started_at`. Interrupted in-process repair → `repair: aborted` /
  `repair_outcome: INTERRUPTED` (does **not** increment `repair_attempts`).
  **Orphan `repair: started` across verify runs:** row-walk reconciles against
  `opsx/opsx_runtime/run_dialogs/<change>/<uuid>/scenario_test_repair` meta
  (`sdk_status`). If the leased repair LLM hop is still **running** → PAUSE
  `REPAIR_STILL_ACTIVE` (no second repair). If the repair run is **finished** or
  absent (legacy rows without `repair_run_uuid` and no running repair anywhere) →
  journal abort + normal audit→repair. Unresolved in-flight repair in the **same**
  verify `run_uuid` with no dialog evidence → PAUSE `REPAIR_OUTCOME_UNKNOWN`.
  Within a single run, repair exception still surfaces `REPAIR_OUTCOME_UNKNOWN` but
  journal must not remain stuck in `started`. Logs SHOULD include row index `N/M`
  and `scenario_id`.
