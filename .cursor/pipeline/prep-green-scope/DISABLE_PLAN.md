# DisablePlan — /opsx-prep-green-scope (baseline normalizer)

## Source of truth

DisablePlan **only** from canonical **`/opsx-static-check FULL`** machine handoff
(`.cursor/static-check/contracts/prep_green_full_handoff.py` →
`FullStaticCheckResult.from_static_check_handoff`). See `full_check.py`.

Forbidden:

- unit-only `pytest -m unit` inventory substituting FULL;
- duplicate registry / «FULL-like» shell graphs;
- **`/opsx-test-env UP`** or infra repair as prep-green recovery;
- Owner decision on undeclared destructive policy.

## Canonical rule

- **`PASS`** → keep enabled.
- **Any other terminal** on a scheduled check → collect disable candidates from
  `static_problems` / `test_problems` when present.

**Pytest waves (`pytest-targeted`, `pytest-unit`, `pytest-integration`,
`pytest-destructive`):**

| Outcome | DisablePlan |
|---------|-------------|
| `FAIL` with failed node ids in handoff | **Exactly** those `test_problems[].node` values |
| `FAIL` executed but node ids missing | **`BLOCKED_INCOMPLETE_HANDOFF`** — repair fan-in; do not skip passing tests |
| `BLOCKED` / `NOT_EXECUTED` / `ERROR`, `suite_not_executed`, zero executable tests | Whole-wave marker inventory via collect-only fallback |

## Check graph (FULL mode)

From `.cursor/static-check/registry.yaml`:

1. `pyright-strict`, `mypy`, `pytest-targeted`
2. `pytest-unit`, `pytest-integration`, `pytest-destructive` (all run in FULL even if earlier FAIL)

## Extraction

| Check | Candidates (non-PASS with problems) |
|-------|-------------------------------------|
| pyright-strict | `static_problems[].path` (file, no broad dirs) |
| mypy | modules from `static_problems[].path` |
| pytest-targeted / unit / integration / destructive | `test_problems[].node` |

## Probe gate (before GO)

| Gate | When |
|------|------|
| `AWAITING_GO` | Handoff complete; every **required** check id present in handoff |
| `BLOCKED_INCOMPLETE_HANDOFF` | Truncated findings / `handoff_complete=false` |
| `BLOCKED_INCOMPLETE_PROBE` | Required check id **missing** from handoff (not env BLOCKED) |
| `BLOCKED_MISSING_FULL_HANDOFF` | No valid JSON handoff |

**Destructive (`pytest-destructive`):**

| Declared `quality.static_check.full.pytest_destructive` | Completeness |
|--------------------------------------------------------|--------------|
| *(missing in capabilities)* | prep-green **does not infer**; handoff packet governs |
| `not_applicable` / `required` | identity / artifacts only |

## Apply (GO)

One bounded delta = candidates from current plan slice. Materialize via `apply_disable.py` only.

## Convergence

Terminal success only when rerun FULL `STATUS: PASS`, `remaining_disable_candidates == 0`.
`blocked_required_checks` is always **0** for baseline normalizer (infra non-PASS is not a
separate blocker). PAUSE when apply unsupported or handoff invalid.

Second FULL **must** be a distinct execution identity (`fingerprint` ≠ prior apply baseline).

## Scenario inverse (outside STAGE1/GO)

Prep-green suppressions must not hide change-required evidence — see
`required_static_check_gate.py` and `DISABLED_PROOF_SKIP` / `REQUIRED_STATIC_CHECK_DISABLED`
in scenario audit.
