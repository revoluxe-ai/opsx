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

## ChatGPT-plan Codex read-only provider (experimental)

Codex app-server with host-side OPSX tool_call_guard can replace Cursor for the read-only next-check model hop:

    python -m tools.langgraph_runtime.cli run --change CHANGE --provider codex --codex-model gpt-6-luna --execute

Prerequisites: install the official Codex CLI and authenticate using
codex login (Sign in with ChatGPT). The adapter enforces the ChatGPT login
status, strips API-key environment variables, uses gpt-6-luna at reasoning
effort low, and never falls back to a different model or billable API key.

Important: LangGraph verification_executor remains BLOCKED for Codex with
CODEX_WRITE_AUTHORITY_UNPROVEN; this V0 bridge does not replace the entire
verification pipeline. Codex built-in shell, patch and MCP tools are disabled.
The adapter offers only explicitly requested host-side dynamicTools with OPSX
tool_call_guard: read, grep, glob, ls, and edit. No general shell access.

Use a unique run id for a new verification cycle; existing checkpoints are
provider-independent only when the stored authority/revision contract agrees.
Do not resume an in-flight Cursor dispatch with Codex. An uncommitted OPSX
change or an expired Codex session must FAIL/PAUSE rather than switch provider.


## OPSX provider selection

The existing Cursor SDK remains the default provider. Opt in explicitly:

    OPSX_ADAPTER_MODULE=agents.adapters.codex_app_server

For the durable verification LangGraph CLI use `--provider codex`; the
provider and model are checkpoint-bound. The Codex adapter accepts the same
`_run_turn(state, prompt, tools, tool_call_guard, read_scope, agent_key)`
contract as Cursor.

Codex read/grep/glob/ls/edit calls pass through the existing OPSX guard.
The edit tool is enabled only in a separate linked Git worktree, for an explicit
restrict_write_allow scope and with OPSX_READ_SCOPE_MODE=enforce. The host
maintains an exclusive cross-process file lock and a SQLite before-image journal
OUTSIDE the worktree. Every edit commits its before-image before writing;
on failed turns/crashes a subsequent attempt restores only identity-matched
bytes. Concurrent edits and unknown revisions fail closed instead of being
overwritten. A successful host turn confirms its provisional edits.

This is bounded worktree edit authority, NOT a general Windows process sandbox.
Codex runs from an empty temporary cwd, sandbox read-only, native shell/patch
disabled. The host-side dynamicTool is the ONLY supported write primitive.
Full OS write isolation, subprocess-based test authority, and end-to-end
workflow replay evidence remain separate acceptance gates.

Do not expose arbitrary shell, apply_patch, unscoped file writes, or run_pytest
as a free-form command. Any run_pytest integration must use the existing
bounded OPSX custom tool contract, with independent execution/evidence scope.
