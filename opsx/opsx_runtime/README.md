# OPSX LangGraph — Bugbot single-node real project runner

Run on the current committed feature branch against local `stage`:

```powershell
.venv\Scripts\python.exe -B opsx\opsx_runtime\run.py
```

Entry path: `run.py` → `workflows/main.py` → `workflows/main_graph.py`
→ `BugBotAgent` (one graph node) → `build_review_adapter()` (provider registry port).

The prompt is **exactly** `agents/verify/bugbot/prompt/get_prompt.py`.
No precomputed diff, additional system message, or appended instructions are
supplied: the model must inspect `stage...HEAD` itself using Git. It has
read/grep/glob/ls/shell tools; shell is enabled when the runner sets
`allow_git_shell=True` on the adapter.

The runner uses the **current Git worktree** as the Cursor agent workspace.
Artifacts land under `opsx/runs/bugbot/real-<id>/` (no nested `repository/`
clone). Uncommitted tracked changes in the worktree are visible to the agent.

Per-run outputs:

- `execution.json`: commit IDs, model, SDK run ID, token counts, billing.
- `review.json`: Pydantic-validated findings/status.
- `checkpoints.sqlite`: LangGraph checkpoints.
- `raw-response.txt`: response retained if initial JSON validation fails.

### Cost reporting

Counts come from `run.usage`, with separate input/output/cache fields and
`total_tokens`. SDK `agent.get_usage()` provides actual billed USD cents
(`raw_cost_cents` and `charged_cents`) **only when Cursor exposes billing**.
If the account rejects that API as `feature_unavailable`, `cost` is null
and `cost_status` is `unavailable` — this is not a reported zero charge.

### Revalidate a completed SDK response without making a new model call

```powershell
.venv\Scripts\python.exe -B opsx\opsx_runtime\run.py --reconcile opsx\runs\bugbot\real-<id>
```

This replays the saved SDK response into the same one-node LangGraph and
checkpoint, without calling the provider. A single Markdown JSON fence is
stripped at the result-parser boundary; the underlying prompt remains unchanged.

### No-cost tests / fixture runner

```powershell
.venv\Scripts\python.exe -B -m pytest -q opsx\opsx_runtime\tests
.venv\Scripts\python.exe -B opsx\opsx_runtime\run.py --dry-run
.venv\Scripts\python.exe -B opsx\opsx_runtime\run_one_node.py --live
```

The last command is a separate legacy test fixture run, which **does** send a
paid model message. It is not used for reviews of the real project.
