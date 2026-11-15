# Verify Phases (shared module)

## Purpose

Общий READ_ONLY модуль фаз проверки OpenSpec change.

Не отдельная пользовательская команда. Его вызывают:

- `/opsx-verify` (тонкий switch `mode=`);
- будущая отдельная команда post-apply DoD (на базе этого модуля).

Authority split:

- этот файл — **фаза / маркеры / что проверять / NEXT_ACTION**;
- `openspec/policies/VERIFY_POLICY.md` — archive policy + blockers;
- `.cursor/commands/opsx-verify.md` — полный archive-audit procedure
  (вызывается только в `PHASE_ARCHIVE`);
- `/opsx-test … verify` — **другой** verify (bug GREEN); не путать.

## Mode selection

Parse from user args / command text:

```text
mode=impl | mode=archive
```

Rules:

- omitted `mode=` → **default `archive`** (backward compatible) + one-line WARN:
  prefer explicit `mode=impl` or `mode=archive`;
- unknown mode → stop; ask Owner to pick `impl` or `archive`;
- `mode=impl` MUST NOT emit `✅ GO` / `❌ NO_GO`;
- `mode=archive` MUST NOT emit `✅ IMPL_OK` / `❌ IMPL_GAPS` as final markers;
- only `mode=archive` + `✅ GO` authorizes starting `/opsx-archive`;
- that `GO` does not promote an ADR. Promotion also needs the separate
  archive authorization in `.cursor/pipeline/contracts/ADR_OWNER_APPROVAL.md`.

## PHASE_IMPL — post-apply DoD

### Question

Сделали ли owed Current Increment (tasks / spec / design / decision)?

### When

Immediately after `/opsx-apply` `all_done` / `impl_done`.
Before long quality chain (pyright / ecc / …) when production `.py` changed.

### Read (bounded)

Change `proposal`, `design`, `tasks`, delta/main specs, proposed/accepted
decisions cited by the change; SEMANTIC_WRITE_TARGET fields if high-risk write;
targeted tests named by tasks.

Do **not** require `EXTERNAL_CALL_COVERAGE_REPORT` /
`PERFORMANCE_REVIEW_REPORT` / static-check PASS as blockers in this phase
(absence → note soft; not IMPL_GAPS alone).

### Must check

1. **IMPLEMENTATION_TRACK** only (see `TASK_GENERATION.md` §0): every apply
   checkbox `[x]` with evidence (no checkbox theater). Post-impl Owner/ops lines
   without checkboxes do **not** belong here. **Zero** open IMPLEMENTATION_TRACK
   `- [ ]` required for `IMPL_OK`.
2. Behavior vs IN_SCOPE / delta-or-main spec scenarios owed by the change.
3. Architecture placement vs cited decisions; no FORBIDDEN adapter on planned path.
4. SEMANTIC_WRITE_TARGET: `RESOLVED` durable fields when applicable; unresolved →
   `IMPL_GAPS` (same class as archive blocker, but phase marker differs).
5. Targeted tests **named by IMPLEMENTATION_TRACK tasks** when those tasks claim
   test proof — not a full-repo or full-scenario-suite gate in this phase.
   **Scenario matrix / row audit / PA live** are later verify hops — not
   `IMPL_GAPS` here unless an IMPLEMENTATION_TRACK task explicitly requires
   that proof and is still open.
6. OUT_OF_SCOPE / FUTURE_INCREMENTS not implemented as DoD creep
   (`EVOLUTION_SCOPE_DRIFT` → gaps).

### Split-APPLY completion projection

When split-APPLY receipts are present, classify implementation completeness
from current machine evidence rather than task-checkbox appearance.

Gap vocabulary:

- `PRODUCT_IMPLEMENTATION_GAP` — required product behavior is not proven current.
- `PROCESS_COMPLETION_PROJECTION_GAP` — product proof is current but legacy
  process/check-box projection is incomplete.
- `TEST_COVERAGE_ARTIFACT_GAP` — planned test artifact/equivalent evidence is missing.
- `STALE_EVIDENCE` — receipt identity no longer matches current inputs.
- `SPLIT_APPLY_CURRENT_GREEN` — covering split slices have current GREEN evidence.

A `PROCESS_COMPLETION_PROJECTION_GAP` alone MUST NOT manufacture an
implementation gap. The projection is informational unless product/test
evidence is also missing or stale.

Use `opsx_tools/opsx-apply-verify-completion.mjs` for deterministic
classification (receipt context only). **`mode=impl` / LangGraph `impl_verify`**
is **read-only** (planning artifacts + code audit via LLM; no pytest subprocess).
**Do not** block `IMPL_OK` for `STALE_EVIDENCE` / `STALE_GREEN` or missing
test-file presence alone (WARN in receipt). **Do not** require `/opsx-apply-green`
before re-verify for staleness only.

### Must not

- Claim archive ready.
- Emit `✅ GO` / `❌ NO_GO`.
- Edit files.
- Run full quality-chain re-audit as hard blockers.

### Output (required)

```text
## Verify phase
mode=impl

## Итоговый вердикт
IMPL_OK — DoD change выглядит выполненным; archive ещё нельзя.
```

or:

```text
## Итоговый вердикт
IMPL_GAPS — DoD не закрыт; сначала apply/update.
```

Sections (short):

```text
## Поведение
## Архитектурное размещение
## Тесты (DoD slice)
## Согласованность tasks
## Archive
Archive ready: no
Reason: mode=impl never authorizes archive

## Следующая команда
```

Then **LOCAL_HOP_COPY_BLOCK** (required — see below) **before** the final
marker. Bare `NEXT_ACTION` / `## Следующая команда` alone is **not** a
sufficient Owner-local hop.

Final line exactly one of:

```text
✅ IMPL_OK
```

```text
❌ IMPL_GAPS
```

### NEXT_ACTION after PHASE_IMPL

**Verification plan spine (TARGET bounded-verify)** — preferred after `IMPL_OK`:

```text
NEXT_ACTION: /opsx-compose-verification-plan change=<change> profile=static_fast
THEN: /opsx-plan-verification change=<change> → /tempo-verification change=<change> low low
THEN: /opsx-verify mode=archive <change>
DO_NOT_DO_YET: /opsx-archive ; manual per-check ladder as substitute for frozen plan
```

`full_required` profile (all mandatory obligations) is **FAIL_CLOSED** until
registry adapters exist for non-static obligations:

```text
BLOCKER: PLAN_COVERAGE_INCOMPLETE
NEXT_ACTION: implement S5 adapters OR use profile=static_fast (static-only; not full completion)
```

Legacy manual quality chain (`/opsx-pyright-strict-coverage` → ecc → antipattern →
static-check) remains **ADVISORY** when verification plan spine is not ready;
do not treat it as `ALL_REQUIRED_CHECKS_PASS` or archive-ready.

`IMPL_OK` + no production `.py` (legacy short path until plan spine default):

```text
NEXT_ACTION: /opsx-compose-verification-plan change=<change> profile=static_fast
DO_NOT_DO_YET: /opsx-archive before tempo + mode=archive verify
```

`IMPL_GAPS`:

```text
NEXT_ACTION: /opsx-apply <change>
(split-aware: product gaps only — stale GREEN is informational, not a blocker)
ALSO_OK: /opsx-update <change>
DO_NOT_DO_YET: /opsx-archive ; quality chain as substitute for DoD
```

### LOCAL_HOP_COPY_BLOCK (mandatory after PHASE_IMPL)

When the recommended next step is another `/opsx-*` hop in **this** Cursor
workspace (typical after `IMPL_OK` or `IMPL_GAPS`):

1. Keep verify report sections as above.
2. After `## Следующая команда` / bare `NEXT_ACTION` lines, and **before**
   the final marker (`✅ IMPL_OK` / `❌ IMPL_GAPS`), emit exactly **one**
   fenced copy-paste prompt (`LOCAL_HOP_COPY_BLOCK`) whose body is plain
   text and already contains:
   - workspace path (absolute);
   - change name;
   - verify `mode=` + marker (`IMPL_OK` | `IMPL_GAPS`);
   - 1–3 gap/blocker lines when `IMPL_GAPS` (or notable caveats on `IMPL_OK`);
   - exact next command (from NEXT_ACTION above);
   - `DO_NOT_DO_YET` / `ALSO_OK` lines when applicable.
3. Outside that fence (except the required final marker line): at most
   **one** status line.
4. Forbidden for this local hop: Web four-block form (`Ссылка` / `Папка` /
   separate path blocks outside the fence).
5. Bare `NEXT_ACTION:` remains inside the verify report for machine/scan
   readers; it **does not replace** `LOCAL_HOP_COPY_BLOCK`.
6. Final marker stays the **last line** of the verify report (VERIFY_POLICY
   `final_marker.position: last_line` / PHASE_IMPL markers).

Example shape (values filled from the run — do not leave placeholders):

````text
```text
Workspace: <abs-path>
Change: <change-name>
Prior: /opsx-verify mode=impl → <IMPL_OK|IMPL_GAPS>
Gaps: <1–3 lines or none>
Command: <exact next /opsx-…>
DO_NOT_DO_YET: /opsx-archive
```
````

Then the final line:

```text
✅ IMPL_OK
```

or:

```text
❌ IMPL_GAPS
```

## PHASE_ARCHIVE — pre-archive gate

### Question

Можно ли archive? **Primary:** required verify hops have green receipts.
**Secondary:** policy blockers (PA, VERIFY_POLICY) — not a second full impl audit.

### When

After quality chain (or short path when no prod `.py` after `IMPL_OK`),
before `/opsx-archive`.

### Procedure

1. Announce `## Verify phase` / `mode=archive`.
2. **Receipt rollup (primary CRITICAL path):** LangGraph / change `verify/`
   receipts and machine preflight — impl, scenario coverage, coverage
   activation, gate_repair, bugbot, security as applicable; stale or missing
   required hop → `NO_GO`. Do **not** re-run a full semantic impl-vs-plan
   audit if `IMPL_OK` receipt is current.
3. **Destructive cache soft gate:** if last FULL was `CACHE_MODE=dev` /
   `DESTRUCTIVE_CACHED>0` / no `CACHE_OFF|FORCE` receipt → WARN
   `DESTRUCTIVE_CACHE_DEV_BEFORE_ARCHIVE` (soft; not sole `NO_GO`).
4. **Task completeness:** only **IMPLEMENTATION_TRACK** checkboxes
   (`TASK_GENERATION.md` §0). Post-impl Owner/ops bullets are **not**
   archive CRITICAL.
5. **Product Acceptance (kernel DONE):** follow
   `PRODUCT_ACCEPTANCE.md` via evidence / channel gate — not re-deriving
   product semantics from scratch.
6. **Drift since IMPL_OK:** bounded check only when receipts show material
   change; prefer verify hop receipts over ad-hoc full-suite pytest here.
7. Final markers only from VERIFY_POLICY: `✅ GO` / `❌ NO_GO`.
8. `## Готовность к archive` + pasteable next command (cache WARN, PA, receipt
   gaps if any).

### NEXT_ACTION after PHASE_ARCHIVE

`✅ GO`:

```text
NEXT_ACTION: /opsx-archive <change>
```

`❌ NO_GO`:

```text
NEXT_ACTION: /opsx-apply <change>
ALSO_OK: /opsx-update <change>
```

If DoD itself regressed, after repair prefer re-run
`/opsx-verify mode=impl <change>` before another archive verify.

### LOCAL_HOP_COPY_BLOCK (mandatory after PHASE_ARCHIVE)

Same rules as PHASE_IMPL `LOCAL_HOP_COPY_BLOCK`: when the next hop is a local
`/opsx-*` in this workspace (`/opsx-archive` on `✅ GO`, or `/opsx-apply` /
`/opsx-update` on `❌ NO_GO`), emit one fenced Owner-local prompt **before**
the final marker, with workspace, change, `mode=archive` + marker, blockers
(on `NO_GO`), exact Command, and applicable `DO_NOT_DO_YET` / `ALSO_OK`.
Bare `NEXT_ACTION` / `## Следующая команда` alone is insufficient. No Web
four-block for this hop. Final marker remains the last line.

## Gate-repair highway (verify loop — repair authorization)

Default OPSX verify gate-repair path (`opsx/opsx_runtime/workflows/verify/gate_repair/`):

- **group_error** builds `analysis_result` **deterministically** from `quality_gate_diagnostics` (one finding → one `grouped_errors` row). LLM grouping is **opt-in** only.
- **repair_order** sorts units in **baseline** `grouped_errors` order (harness priority), without LLM. LLM sort is **opt-in** only.
- **«Всё проходит»** here means the loop **reaches repair dispatch** with materialized handoff — not that gates are green in a single iteration.

Opt-in environment flags (default unset / off):

| Flag | Effect |
|------|--------|
| `OPSX_GROUP_ERROR_LLM=1` | Enable LLM `group_error`; on partition mismatch, **fallback** to gate-only triage (still emits `analysis_result`). |
| `OPSX_REPAIR_ORDER_LLM=1` | Enable LLM `repair_order` sort when `group_error` was LLM (`finished`). |

State overrides: `group_error_mode=llm`, `repair_order_mode=llm`.

Quality gates pytest scope matches product default: `tests/` only via `tests/pytest_workflow_argv.build_workflow_pytest_argv` (wired from `build_quality_gate_commands` and `build_collect_command`), active suite `-m "not requires_postgres"`. Выражение живёт в `opsx/opsx_runtime/agents/common/test_tier_policy.py` (`ACTIVE_EXPR`). Meta-процесс OPSX не переписывает файлы `tests/**`.

### infra_tests (после bugbot, до security)

Единственная стадия, которая исполняет tier `requires_postgres`. Узел `infra_tests` сам решает ADOPT/START/START_ALT_PORT/UNAVAILABLE по `opsx/project/infra/infra-profile.yaml`. `FAIL` и `BLOCKED_ENV` поднимают `InfraTestsStageError`; security после этого не стартует. Зелёный путь детерминированный (0 вызовов модели). Продуктовые тесты стадия не редактирует: node id приходят из state. Живой smoke только при `OPSX_INFRA_LIVE_SMOKE=1` и отдельном GO Owner.

### Environment prep (first-pass harness + test env)

После `group_error` при mass `WF_PYTEST_COLLECTION` / priority cohorts (Hermetic, shared fixtures):

- узел **`environment_prep`** (`VerifyEnvironmentPrepAgent`) — один LLM-hop на когорты, правки `tests/**`, collect proof, опциональный re-run `quality_gates`;
- лимит: **2** попытки за outer iteration (`MAX_ENVIRONMENT_PREP_ATTEMPTS`);
- opt-out: **`OPSX_SKIP_ENVIRONMENT_PREP=1`**;
- снятые findings → `environment_prep_resolved_finding_ids` (сжатие `repair_order`).

## Consumer hint matrix (embed in companion commands)

Canonical post-impl spine (single NEXT — no parallel ladders):

| Producer | Pasteable NEXT when success |
|---|---|
| `/opsx-apply` all_done | `/opsx-verify mode=impl <change>` |
| verify `mode=impl` IMPL_OK | `/opsx-compose-verification-plan change=<change> profile=static_fast` |
| verify `mode=impl` IMPL_GAPS | `/opsx-apply <change>` |
| compose `profile=full_required` unsupported | `BLOCKER: PLAN_COVERAGE_INCOMPLETE` — **not** `static_fast` as “full” |
| compose + plan-verification FROZEN + tempo complete | `/opsx-verify mode=archive <change>` |
| verify `mode=archive` GO | `/opsx-archive <change>` |

Legacy manual quality chain (`/opsx-pyright-strict-coverage` → ecc → antipattern → static-check)
is **ADVISORY** only when verification plan spine is not used; it does **not** satisfy
`ALL_REQUIRED_CHECKS_PASS` and must not replace compose → freeze → tempo.

Never point quality-chain success at bare `/opsx-verify` without `mode=`
when the phase is known; prefer `mode=archive` after frozen plan completes.

## Future separate command

A dedicated post-apply DoD command MAY later wrap **only** `PHASE_IMPL`
from this module without duplicating rules. Until then `/opsx-verify mode=impl`
is the sole front-door for PHASE_IMPL.
