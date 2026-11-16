# OPSX Web / API foundation (Phase 1 contract)

## Authority model

- **CLI and Web share** `RunService`, `installer`, `doctor`, `config_commands`, and `project_commands`.
- **Write paths** (`POST /api/workflows`, install, upgrade, migration apply) require the same execution lease and authority transitions as CLI.
- **Read paths** may stream evidence but must not invent PASS without `verification_status`.

## HTTP surface (current + reserved)

| Operation | Method | Idempotency | Service |
|-----------|--------|-------------|---------|
| Health / identity | GET /api/health | yes | panel state |
| Project profile | GET /api/project | yes | `project_show` |
| Installation metadata | GET /api/config/installation | yes | `config_show` |
| Run history | GET /api/runs | yes | `RunService.history` |
| Run detail + SSE | GET /runs/{id} | yes | status.json |
| Start workflow | POST /api/workflows | non-idempotent (run_id) | `RunService.execute` |
| Repair | POST /repair | non-idempotent | `RunService.execute` |
| Shutdown | POST /api/shutdown | yes | panel owner |

Reserved Phase 2 (same backend, no duplicate engine):

- migration plan/apply endpoints wrapping `migration.build_plan` / `apply_plan`
- config validate, project validate
- upgrade/install with explicit non-idempotent policy + receipt polling

## Run record schema (version 1)

```json
{
  "process_exit_code": 0,
  "process_status": "COMPLETED",
  "workflow_status": "WORKFLOW_TERMINAL",
  "verification_status": "PASS",
  "status": "PASS",
  "exit_code": 0
}
```

UI and API must treat `verification_status` as the gate. `process_exit_code` is diagnostic only.

## Security

- Loopback bind (`127.0.0.1`) only by default.
- Token middleware on all mutating routes.
- No bypass of machine gates in the panel HTML.

## Next independently testable slice

Ship a router module `opsx_cli/api_router.py` that mounts reserved endpoints on the existing FastAPI app without changing the execution engine.