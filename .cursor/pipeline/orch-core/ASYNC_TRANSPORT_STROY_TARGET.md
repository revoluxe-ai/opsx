# STROY target addendum — async transport binding

**Binds:** `OPSX-ORCH-ASYNC-TRANSPORT-01` → `OPSX-TARGET-ORCH-RUNTIME-01`  
**Does not replace:** `.cursor/pipeline/orch-core/ASYNC_TRANSPORT.md` (GAME source semantics)

## Mapping

| GAME realization | STROY realization |
|------------------|-------------------|
| `TASK_BACKGROUND` primary executor | Parent Temporal workflow step + Cursor executor harvest |
| Host inline fallback | In-parent sync helper `orch_bug_fix.py` admission only |
| Lock / ETA / SSCA rules | Unchanged — enforced by orch-core + mission receipts |
| Nested workflow start | **FORBIDDEN** — no second async orchestrator |

## Hard bans (STROY)

- `START_TEMPORAL` from this command or `orch_bug_fix.py`: **NO**
- `transport=TASK_BACKGROUND` on sync admission helper: **FAIL_CLOSED** with reason `SECOND_ASYNC_TRANSPORT_FORBIDDEN`
- Product writes outside delegated bug primitives: **FORBIDDEN**
- Synthetic `BUG_BATCH_CLEAR` without fresh durable close evidence: **FORBIDDEN**

## Evidence identity

`execution_id`, `head`, and mission folder under `opsx/runs/orch-missions/<MISSION_ID>/`
must match the active parent workflow binding. Stale mission/execution → fail closed.

## STROY persist mapping (execution authority over donor GO paths)

When the GAME `/opsx-orch-bug-fix` body says `DELEGATED_PERSIST` `/opsx-bug-family-plan GO`
or `DELEGATED_PERSIST` `/opsx-bug-disposition GO`, the in-parent sync helper returns
`STATUS: DELEGATED_PERSIST` and the parent host runs the matching READ_ONLY command once
(no `GO` token, no Owner wake). Durable batch close remains S5b
`/opsx-materialize-bug-queue` (host-executed). Family-plan SSCA on STROY is in-turn plan
output only (not `family-plans/**` files). Disposition SSCA is the existing case ledger
under `opsx/runs/bug-cases` (not `dispositions/**`). `NESTED_TEMPORAL_START` stays `NO`.
`OWNER_WAKE_ON_DETERMINISTIC_TRANSITION=NO`.
