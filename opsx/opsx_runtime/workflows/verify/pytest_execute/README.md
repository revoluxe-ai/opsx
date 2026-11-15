# pytest_execute workflow

Единственная точка прогона pytest на scope: collect → harness repair (optional) → run.

Публичный API в `invoke.py`:

- **`await run_pytest_execute(...)`** — LangGraph / verify nodes (harness + CommitAgent в том же event loop).
- **`run_pytest_execute_sync(...)`** — CLI, live_checks, cohort verify (`max_harness_attempts=0`); only when **no** running asyncio loop.
