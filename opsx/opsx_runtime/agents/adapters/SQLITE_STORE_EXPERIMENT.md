# Provider agent SQLite recovery store — opt-in experiment

Status: **EXPERIMENTAL / NOT LIVE-CERTIFIED**. No automatic migration, deletion, or reset.

## Failure signal

The existing JSONL checkpoint store scans large checkpoint files on lookup (Cursor confirmed
this in August 2026). The shared store at `tools/cursor_executor/.bridge-state/jsonl-store`
can grow across OPSX executions, while running verifications need durable SDK identities.
Replacing the store without a reconciliation plan can orphan unfinished SDK runs.

## Configuration and authority

- Default: store backend env unset → existing `jsonl` backend and original paths
  (see ``tools/cursor_executor/cursor_executor/bridge_local.py``).
- Opt-in: set store backend to `sqlite` for isolated experiments only.
- Invalid values fail closed; SQLite requires an explicit, stable `store_scope`.
- Provider bridge and every read/repair adapter receive identical `{"type":"sqlite"}` config.
- SQLite state root: `tools/cursor_executor/.bridge-state/sqlite/<sha256(scope)>`.
- `verify_impl` uses a stable **per-change recovery scope** so a restarted verify can
  reconcile original agent/run IDs; bugbot uses an execution-scoped `thread_id`.
- No automatic reading of old JSONL rows from SQLite. Starting a new SQLite scope
  fails closed with `CURSOR_SQLITE_LEGACY_STATE_PRESENT` if legacy JSONL entries
  exist in this executor installation; no state is deleted. Existing initialized
  SQLite scopes remain resumable.
- This conservative guard does not classify which historical runs are terminal.
  Run SQLite experiments only in an isolated clean executor installation;
  separate migration/reconciliation policy is required for mixed history.
- No filesystem removal, retention, or fallback to in-memory/JSONL on SQLite errors.
  Crash or corrupt SQLite state must PAUSE/FAIL with recovery evidence; never claim PASS.
- Existing `CursorBridgeRuntime` owns bridge creation/teardown, including transport
  generation retirement; this change does not modify that lifecycle or the adapter tier.

## Deterministic acceptance

1. Legacy JSONL config/path is unchanged with the feature flag absent.
2. Same recovery scope uses identical SQLite root across process restarts; different scopes
   never share the root; scope cannot cause path traversal.
3. Bridge and SDK agent/repair options reference the same backend.
4. Missing/invalid scope or backend fails before a remote SDK call.
5. Legacy JSONL state is never silently moved/deleted.
6. In live-agent isolated dogfood: 1/4/8/16 agents; verify SQLite Node runtime support,
   persistence across restart, concurrent checkpoint writes, original-run GetRun/reconcile,
   clean shutdown, provider throttle/circuit-breaker and bounded memory/latency.
7. Verify restart against an unresolved historical JSONL run is blocked from SQLite
   until a separate migration/reconciliation policy is approved.

## Local offline checks

Root `pyproject.toml` registers both local OPSX import roots with pytest, so the
subproject `cursor_executor` is importable without installing it separately.
From the repository root, using Poetry (not the system Python):

```bash
poetry run python -m pytest tools/cursor_executor/tests/test_bridge_local_store.py -q
poetry run python -m pytest opsx/opsx_runtime/agents/adapters -k test_cursor_bridge_runtime -q
```

On Windows/Git Bash, a broken system `python` may fail to initialize before
pytest; this is unrelated to SQLite or the test suite. Do not add the provider wheel
to Poetry to fix `ModuleNotFoundError: cursor_executor` — it is a different local package.

No live agent test, large-agent load, migration or cleanup was executed for this PR.

References:
- https://forum.cursor.com/t/jsonllocalagentstore-checkpoints-get-rescans-whole-file-making-every-send-quadratic/167617/5
- https://github.com/cursor/sdk-bridge/blob/main/docs/protocol.md
