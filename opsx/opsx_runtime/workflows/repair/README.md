# LangGraph Bug Team / repair

Standalone OPSX orchestration restored from the project requirements, donor Bug Team contracts, surviving LangGraph runtime, and dogfood state.

## Boundaries

- Separate subsystem: not imported by workflows/main_graph.py.
- No push, deploy, or external production mutation.
- Local commit handoff is handled by `agents/common/commit_agent.py` after file-changing nodes.
- Existing .cursor/pipeline/bug-team worker contracts remain role authority.
- LangGraph owns execution state, transitions, checkpoints, interrupts, and composition.
- Every AI hop returns one JSON object validated by the role's Pydantic schema (Bugbot-style).
- Performers for standalone AI hops live under `agents/repair/` (for example
`fix_bug/`, `green_verifier/`, `invariant_capture/`, `bounded_hunter/`); workflow modules only orchestrate them.

## Preflight

Every live repair/Bug Team graph starts with deterministic PRE-FLIGHT before any agent token is spent. It checks/prepares:

- writable Git worktree (`workspace`, usually same as `repository_path`) and free disk;
- Python 3.11-3.14 runtime;
- LangGraph, cursor-sdk, project and test dependencies;
- Cursor authentication and exact live model route;
- project unit-test collection plus an executed pytest smoke;
- deterministic pytest wrapper with safe PYTHONPATH/test environment;
- optional Docker integration capability: disposable PostgreSQL 55432, preview PostgreSQL 5433, Redis 6390, ClickHouse 8124, disposable-DB marker and Alembic migration.

If unit runtime is not READY, CLASSIFIER/RED are never started. Integration services are a separate capability: an agent that needs an unavailable integration lane must return BLOCKED instead of fabricating test evidence. Use --prepare-integration to start/prepare the stacks and --require-integration to make them mandatory.

The generated preflight report is persisted as opsx/runs/repair/<mission-id>/preflight.json and is included in the hop INPUT CONTEXT when present. Agents must use preflight.test_runtime.pytest_prefix rather than guessing a python/pytest command.

## Composition

BUG TEAM:
materialize -> claim -> repair subgraph -> hunt subgraph -> relation -> queue/fanout -> repeat -> fan-in -> final owner verdict.

Repair subgraph:
CLASSIFIER -> RED_TEST_WORKER -> Owner GO/interrupt -> IMPLEMENTATION_EXECUTOR -> COMMIT -> GREEN_VERIFIER -> INVARIANT_CAPTURE_WORKER.

Hunt subgraph:
BOUNDED_HUNTER -> RELATION_CLASSIFIER. SAME_INVARIANT_FINDING candidates are materialized back into the queue and repaired under the same root invariant.

Any layer is callable independently:
- primitive.py runs one primitive.
- graph.build_repair_graph() runs one finding lifecycle.
- graph.build_hunt_graph() runs one invariant hunt.
- graph.build_bug_team_graph() runs the full team.

## Model and token contract

AI hops: ``ModelResolveRequest`` → ``resolve_model`` (данные из ``default_model.py``) → ``repair_adapter_port.model_selection_for_hop`` → ``execute_role`` / ``RepairAdapter``. Receipt ``model_id``.

Default in resolve: claude-haiku-5-5, thinking=true, reasoning_effort=low.

Per-agent observed input limit: 300,000 tokens.
Observed input = input_tokens + cache_read_tokens + cache_write_tokens.
If any component is unreported, observed input remains unknown/null, never zero.
An observed over-limit hop is blocked and persisted in its worker receipt.

## Durability

Each mission is stored below opsx/runs/repair/<mission-id>/ and can contain:
- latest_state.json
- checkpoints.sqlite
- workers/<hop>.json — validated receipt + typed payload summary
- workers/<hop>.prompt.txt — full prompt sent to the model (debug)
- workers/<hop>.context.json — INPUT CONTEXT for the hop (debug)
- workers/<hop>.response.raw.txt — full adapter `result.text` (debug)
- usage.json
- summary.json
- **durable/INDEX.json** — manifest of mission-local exports
- canonical bug identity: `opsx/runs/bug-log/BUG_JOURNAL.ndjson` (`BugJournal`); `bug_id` on `Finding` / `WorkerReceipt`. Projections: `by-change/`, `by-bug/`, `opsx/runs/bug-cases/`
- durable/findings/FINISHED.json, QUEUE.json, optional SOURCE_REVIEW.json
- durable/invariant/ROOT_INVARIANT.json; **family/INVARIANT.json** (mirror)
- durable/hunt/HUNTS.json, RELATIONS.json
- durable/fanin/FANIN.json; **results/FANIN.json** (mirror)
- durable/learning/LEARNING_DECISION.json; **family/** and **results/** mirrors
- durable/workers/MANIFEST.json — sha256 index of workers/*
- legacy `opsx/runs/repair/*/repository/` dirs from older missions only (new runs use the worktree directly)

Structured exports refresh on every ``primitive.py`` write and ``runner.py`` state checkpoint.
Learning JSON is a mission-local draft aligned with bug-team field names until
``FAMILY_LEARNING_WORKER`` is wired; promotion still goes through process commands.

New runs use LangGraph SQLite checkpointing with durability=sync.
Owner questions use LangGraph interrupt/resume.
Legacy latest_state.json can be migrated into a new mission with --recover-state and --restart-stage, so completed stages do not need to be repeated.

## CLI examples

Recommended Windows runtime:

    tools\\langgraph_runtime\\.venv312\\Scripts\\python.exe

Bootstrap that runtime if it must be recreated:

    py -3.12 -m venv tools\\langgraph_runtime\\.venv312
    tools\\langgraph_runtime\\.venv312\\Scripts\\python.exe -m pip install -r tools\\langgraph_runtime\\requirements.txt

Prepare/check environment without launching an agent:

    tools\\langgraph_runtime\\.venv312\\Scripts\\python.exe -B opsx\\opsx_runtime\\workflows\\repair\\runner.py --finding-report <review.json> --preflight-only

Prepare full integration infrastructure as well:

    tools\\langgraph_runtime\\.venv312\\Scripts\\python.exe -B opsx\\opsx_runtime\\workflows\\repair\\runner.py --finding-report <review.json> --preflight-only --prepare-integration --require-integration

Dry materialization:
python -B opsx/opsx_runtime/workflows/repair/runner.py --finding-report <review.json>

Full live Bug Team:
python -B opsx/opsx_runtime/workflows/repair/runner.py --finding-report <review.json> --live --allow-soft-cap --owner-go

Single repair workflow:
python -B opsx/opsx_runtime/workflows/repair/runner.py --mode repair --finding-report <review.json> --live --allow-soft-cap --owner-go

Standalone hunt from a saved state:
python -B opsx/opsx_runtime/workflows/repair/runner.py --mode hunt --recover-state <latest_state.json> --live --allow-soft-cap

Recover an old failed RED run without rerunning CLASSIFIER:
python -B opsx/opsx_runtime/workflows/repair/runner.py --mode repair --recover-state <latest_state.json> --restart-stage red --live --allow-soft-cap --owner-go

Run one primitive:
python -B opsx/opsx_runtime/workflows/repair/primitive.py --state <latest_state.json> --role CLASSIFIER --live

Inspect a mission:
python -B opsx/opsx_runtime/workflows/repair/runner.py --status <mission-id>

## Recovery evidence

The restored schemas can parse the surviving dogfood-5176b2af34 latest_state.json, including migration of legacy role names classify/red and inference of the missing legacy chosen_route.
The historical failure caused by requesting a nonexistent Cursor tool named write is removed: the mutating adapter permits `edit` (legacy `piWrite`/`piEdit` aliases map to `edit` at CreateAgent) plus bounded read/shell tools in the mission worktree.
