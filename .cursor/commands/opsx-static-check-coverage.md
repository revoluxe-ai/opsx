---
description: "Audit changed in-policy production Python against strict typing scope and inverse prep-green suppressions before pyright-strict/mypy on touched code."
argument-hint: "base=<branch|ref> [GO]"
component_id: opsx.commands.static_check_coverage
runtime_class: ACTIVE
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


# /opsx-static-check-coverage

**Universal changed-code coverage gate** before typing static-checks on touched
production Python.

Проверь весь **in-policy** production Python, изменённый между `base` и
**текущим worktree**:

1. путь попал в `pyrightconfig_strict.json` `include` и не вырезан `exclude`;
2. change-path не остаётся под baseline suppression, который поставил
   `/opsx-prep-green-scope` (`pyright ignore` / per-module mypy `ignore_errors`).

Цель: как только change затронул production-файл, baseline-исключение для этого
пути больше не может скрывать его от `pyright-strict` / `mypy`.

## Separation from `/opsx-adopt`

| Contour | Checks |
|---------|--------|
| `/opsx-adopt` VERIFY | `compileall`, pytest active suite, **standard** Pyright ratchet vs baseline |
| **Not** adoption | repo-wide `pyright-strict`, `mypy`, strict-on-touch |
| This command | changed in-policy `.py` ⊆ strict `include` (minus `exclude`) **and not hidden by prep-green static suppressions** |
| After PASS | `/opsx-static-check` typing on changed scope; diagnostics → `/opsx-fix-types` |

## What this is

* Coverage audit: `base` → current worktree changed production paths ⊆
  strict `include` (minus `exclude`).
* Inverse prep-green audit: для changed paths проверяет точные baseline suppressions
  из `opsx/prep-green-scope/plan.json` (`disable_plan.pyright_files`,
  `disable_plan.mypy_modules`).
* Рекомендуемый шаг **после** `/opsx-apply`, **до**
  `/opsx-external-call-coverage`, conditional `/opsx-performance-review`,
  typing static-check и `/opsx-verify`, когда в diff есть production `.py`.
* Опционально `GO`: добавить missing `include[]` и снять **только подтверждённые
  prep-green suppressions**, которые пересекаются с changed production paths.

## What this is NOT

* NOT запуск `pyright-strict` или `mypy` (→ `/opsx-static-check`, `/opsx-fix-types`).
* NOT `/opsx-adopt` VERIFY (no baseline ratchet / full-repo typing here).
* NOT archive verdict (→ `/opsx-verify mode=archive`).

## User input

```text
$ARGUMENTS
```

| Token | Effect |
|-------|--------|
| `base=<ref>` | обязательный comparison base |
| `GO` | apply only current audit gaps: additive `include[]` + remove proven prep-green static suppressions on changed paths |
| иное | `STATUS=BLOCKED`, no writes |

Target всегда **текущий worktree**; отдельного target-токена нет.

Примеры:

- `/opsx-static-check-coverage base=stage`
- `/opsx-static-check-coverage base=stage GO`

Legacy alias (same semantics): `/opsx-pyright-strict-coverage` → use this command id in new work.

## Policy scope (in / out)

**In-policy** (resolve from `opsx/project/capabilities.yaml` `product.source_roots`,
typically `src/stroy/**`):

* changed or added `*.py` under those roots;
* changed top-level production `*.py` at repo root when present in diff.

**Out-of-policy** (`OUT_OF_POLICY_SCOPE`):

* `tests/**`, `.cursor/**`, `openspec/**`, `docs/**`, `k8s/**`, non-`.py`;
* deletions — no include entry required.

## Target resolution

1. Resolve `base` to an immutable commit.
2. Target is always **the current worktree**. Include staged, unstaged and relevant
   untracked in-policy production paths.
3. Compare `base` directly to that worktree snapshot. A clean worktree may be
   equivalent to `HEAD`, but the command still reports `AUDIT_TARGET: WORKTREE`.

Report `AUDIT_TARGET`, `BASE_SHA`, `TARGET_REF: WORKTREE`, worktree counters.

## Matching rules

Normalize paths to repo-relative POSIX.

### Strict include/exclude

Prefix match on `include` / `exclude` as in `pyrightconfig_strict.json`.

### Inverse prep-green suppressions

Read `opsx/prep-green-scope/plan.json` only when it is a recognized
`OPSX-PREP-GREEN-SCOPE-V1` plan and use its exact `disable_plan` lists as
ownership evidence.

For every changed in-policy production `.py` path:

- `PYRIGHT_PREP_GREEN_SUPPRESSED`: path is both in
  `disable_plan.pyright_files` and currently present in
  `pyrightconfig_strict.json ignore[]`.
- Map the production path to its Python module.
  `MYPY_PREP_GREEN_SUPPRESSED`: module is both in
  `disable_plan.mypy_modules` and currently has an exact
  `[[tool.mypy.overrides]]` entry with `ignore_errors = true`.

Do **not** classify or remove suppressions that are not proven by the prep-green
plan. Do not broaden from a changed file to unrelated sibling modules.

With `GO`:

- add only `MISSING_FROM_INCLUDE` entries;
- remove only exact `PYRIGHT_PREP_GREEN_SUPPRESSED` ignore entries;
- remove only exact `MYPY_PREP_GREEN_SUPPRESSED` overrides (or only
  `ignore_errors = true` from that exact override when it contains other
  settings that must be preserved);
- re-audit once;
- do **not** run or repair type diagnostics here. Actual typing failures belong
  to `/opsx-static-check` → `/opsx-fix-types`.

## Status

| STATUS | When |
|--------|------|
| `PASS` | no `MISSING_FROM_INCLUDE`, `HIT_EXCLUDE`, `PYRIGHT_PREP_GREEN_SUPPRESSED`, or `MYPY_PREP_GREEN_SUPPRESSED` on changed in-policy paths |
| `FAIL` | any of those changed-path coverage/suppression gaps exists |
| `BLOCKED` | missing `base`, required config, or git error |

## Final report (required)

Report at least:

```text
STATUS: PASS | FAIL | BLOCKED
BASE: <ref>
AUDIT_TARGET: WORKTREE
BASE_SHA: <sha>
TARGET_REF: WORKTREE
IN_POLICY_CHANGED: <n>
COVERED: <n>
MISSING_FROM_INCLUDE: <n>
HIT_EXCLUDE: <n>
PYRIGHT_PREP_GREEN_SUPPRESSED: <n>
MYPY_PREP_GREEN_SUPPRESSED: <n>
GO_APPLIED: false | true
INCLUDE_WRITTEN: none | <paths>
PYRIGHT_SUPPRESSIONS_REMOVED: none | <paths>
MYPY_SUPPRESSIONS_REMOVED: none | <modules>
NEXT_ACTION:
```

### NEXT_ACTION rules

* `FAIL` без `GO` → `/opsx-static-check-coverage base=<BASE> GO`.
* После `GO` re-audit:
  - если coverage/suppression gaps остаются → report exact remaining gap, STOP;
  - если PASS → `/opsx-static-check FULL` so changed paths are actually checked.
* Typing diagnostics after suppressions are opened → `/opsx-fix-types`.
* Do not route around a changed-path typing failure by restoring prep-green suppression.

## Handoff

| From | To |
|------|-----|
| `/opsx-apply` | this command with `base=` |
| this PASS | external-call → static-check typing → verify archive |
