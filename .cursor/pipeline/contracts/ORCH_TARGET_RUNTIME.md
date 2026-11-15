# ORCH_TARGET_RUNTIME

Contract: `OPSX-TARGET-ORCH-RUNTIME-01`
Also: `OPSX-WB-ORCH-TARGET-01`

Transport (Owner decision): Cursor Python SDK + Temporal is the only
asynchronous orchestration transport. Do not add SOURCE orch-core Task
transport or a second mission bus.

```text
NO_DIRECT_PRODUCT_WRITE=YES
NO_DIRECT_GIT_WRITE=YES
```

This file separates **SYSTEM SEMANTICS**, **REALIZATION**, and **USER CONTRACT**
for `/opsx-orch-bugbot-converge` and `/opsx-orch-bug-fix`.

## SYSTEM SEMANTICS (preserve)

- Outer converge is check → repair batch → recheck until a **fresh** native
  `/review-bugbot` CLEAR, or a bounded stop.
- Inner bug-fix is one admitted **product** batch through intake → plan hint →
  route → opaque repair → durable close when still required.
- Check authority is native `/review-bugbot`. Parent must not imitate Bugbot.
- Product mutation is never performed by the orch wrappers.
- Process/OPSX-contract findings are not admitted to the product bug PP.
- `BUG_BATCH_CLEAR` is not `BUGBOT_CONVERGED`.
- PASS/CONVERGED is forbidden from wrapper self-report. Evidence must be
  identity/freshness-bound. cross-execution and cross-revision receipts are
  rejected. Child prose alone cannot close a work item.
- If the same unresolved causal finding repeats, sibling expansion remains
  unresolved, a regression appears, or a child reports no durable repair
  progress: stop FAIL/PAUSE. Partial execution never silently becomes PASS.
- No commit/merge/push from these wrappers.

## REALIZATION (target)

- Converge outer loop: existing `tools/temporal` bugbot lane
  (`python tools/temporal/runtime.py run bugbot`).
- Inner repair hop: `/opsx-orch-bug-fix` sequences target primitives and
  does not start Temporal.
- Finding partition: `tools/temporal/finding_filter.py` plus
  `.cursor/pipeline/orch-bug-fix/orch_bug_fix.py`.
- Target `/opsx-bug-family-plan` and `/opsx-bug-disposition` have no Python `run()`
  writer here; when GAME issues `DELEGATED_PERSIST` with donor `GO`, the sync helper
  returns `DELEGATED_PERSIST` + `PERSIST_MODE: READ_ONLY` and the parent host runs the
  command once (no `GO`, no Owner wake). Durable close stays `/opsx-materialize-bug-queue`.
- SOURCE `mission=` resume against a second async bus is not the Temporal
  kernel; stale mission/execution identities fail closed.

## USER CONTRACT

See `/opsx-orch-bugbot-converge` and `/opsx-orch-bug-fix` command bodies.
