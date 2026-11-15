# OPSX LangGraph runtime — REAL verification lane

This is a real adapter, not a toy workflow. It reuses:
- Existing /opsx-next-check through the Cursor SDK.
- The exact dynamic CHECK_ASSIGNMENT executor through Cursor SDK.
- Existing bounded-verify plan/ledger/result validation functions.
- Existing deterministic verification_transition_policy.py.
- A disk-persistent LangGraph SQLite checkpointer.
- A durable side-effect dispatch journal with crash fencing.

The code deliberately does not change existing /tempo-* commands,
Temporal workers or OPSX product services. Running this command invokes
real Cursor tasks and may modify project files via existing OPSX executors.

### Commands (from repo root)

Install requirements in a dedicated environment (Python 3.11+):

    .\.venv\Scripts\python.exe -m venv tools/langgraph_runtime/.venv
    tools/langgraph_runtime/.venv/Scripts/python -m pip install -r tools/langgraph_runtime/requirements.txt

Inspect without starting execution:

    tools/langgraph_runtime/.venv/Scripts/python -m tools.langgraph_runtime.cli status --change CHANGE

Run a real frozen verification plan:

    tools/langgraph_runtime/.venv/Scripts/python -m tools.langgraph_runtime.cli run --change CHANGE --execute

Recover after process interruption:

    tools/langgraph_runtime/.venv/Scripts/python -m tools.langgraph_runtime.cli resume --change CHANGE --execute

To start another cycle after a completed run, supply a new --run-id.
--execute must be explicitly provided to run/resume.

### Persistence and safety

- Runtime state: opsx/runs/langgraph/checkpoints.sqlite3.
- Dispatch claim/recovery: opsx/runs/langgraph/dispatch.sqlite3.
- Per-change OS lock prevents concurrent LangGraph invocations on one host.
- durability="sync" checkpoints before proceeding to the next graph node.
- Never retry an IN_FLIGHT executor if no identity-bound durable evidence
  exists; report EXECUTOR_COMPLETION_UNCERTAIN. The operator must reconcile.
- Temporal running concurrently against the same change is NOT protected
  by this lock; never run both runtimes on the same change.

### Scope / not yet delivered

This is the first real verification lane, NOT a complete Temporal shutdown.
Other lanes (bugbot/security/project-bug-hunt) remain in Temporal.
A full migration requires shared cross-runtime exclusivity, a production
PostgreSQL checkpointer for distributed workers, deployment supervision,
end-to-end live acceptance, monitoring and a rollout/rollback policy.
The current verification lane has no Owner GO approval; that remains true.
