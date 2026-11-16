"""Process entrypoint for existing runtime use cases, independent of CLI/HTTP."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys


async def execute_request(request: dict) -> int:
    project = Path(request["project_root"]).resolve()
    core = Path(request["core_root"]).resolve()
    os.environ["OPSX_PROJECT_ROOT"] = str(project)
    os.environ["OPSX_CORE_ROOT"] = str(core)
    settings_file = project / "opsx/project/project.json"
    settings_text = await asyncio.to_thread(settings_file.read_text, encoding="utf-8")
    settings = json.loads(settings_text)
    adapter_module = (settings.get("adapter") or {}).get("module")
    if adapter_module:
        os.environ["OPSX_ADAPTER_MODULE"] = str(adapter_module)
    capability_file = project / "opsx/project/capabilities.yaml"
    if capability_file.is_file():
        import yaml
        capability_text = await asyncio.to_thread(capability_file.read_text, encoding="utf-8")
        capabilities = yaml.safe_load(capability_text) or {}
        if capabilities.get("status") == "NEEDS_ADOPTION":
            raise ValueError("PROJECT_ADOPTION_REQUIRED: resolve this project's capabilities before execution")
        source_roots = (capabilities.get("product") or {}).get("source_roots") or []
        os.environ["OPSX_PRODUCT_SOURCE_ROOTS_JSON"] = json.dumps(source_roots)
    for path in (core, core / "opsx/opsx_runtime", core / "opsx/configs",
                 core / ".cursor/pipeline", core / "tools/cursor_executor"):
        sys.path.insert(0, str(path))
    from opsx.project_context import bind_project_context, reset_project_context
    token = bind_project_context(project, core)
    try:
        if request["mode"] == "workflow":
            from run_verify_impl import execute
            from workflows.verify.cli_outcome import (
                raise_if_implementation_lane_terminal, raise_unless_security_pass,
            )
            outcome = await execute(request["change_id"], repo_root=project,
                                    timeout_seconds=request["timeout_seconds"])
            raise_if_implementation_lane_terminal(outcome, change_id=request["change_id"])
            raise_unless_security_pass(outcome, change_id=request["change_id"])
            print("SECURITY_COMPLETE: PASS", flush=True)
            return 0, {"workflow_status": "WORKFLOW_TERMINAL", "verification_status": "PASS"}
        if request["mode"] != "repair":
            raise ValueError("REQUEST_MODE_UNSUPPORTED")
        ui = core / "opsx/opsx_runtime/ui/named_bug_repair"
        sys.path.insert(0, str(ui))
        from bootstrap import prepare_named_bug_repair_runtime
        from agents.adapters.bridge_runtime_port import open_bridge_runtime
        from intake import run_owner_named_bug_repair
        prepare_named_bug_repair_runtime(repo_root=project)
        async with open_bridge_runtime(project) as bridge:
            outcome = await run_owner_named_bug_repair(
                repository_path=project, bug_text=request["bug"],
                change_id=request["change_id"], timeout_seconds=request["timeout_seconds"],
                bridge_runtime=bridge,
                on_log_line=lambda line: print(line, flush=True),
                on_progress=lambda payload: print(json.dumps({"opsx_event": payload}), flush=True),
            )
        summary = outcome.get("owner_named_check_repair_summary") or {}
        status = str(summary.get("status") or outcome.get("owner_named_check_repair_status") or "").upper()
        print(json.dumps({"repair_result": summary}, ensure_ascii=False), flush=True)
        ok = status in {"PASS", "FIXED", "COMPLETED", "REPAIR_COMPLETE", "COMPLETE_NO_CLASS_LESSON", "COMPLETE_CLASS_LESSON"}
        verify = "PASS" if status in {"PASS", "FIXED"} else ("NOT_READY" if ok else "FAIL")
        return (0 if ok else 1), {"workflow_status": status or "REPAIR_UNKNOWN", "verification_status": verify}
    finally:
        reset_project_context(token)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("A request file is required.")
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    async def owned():
        from .context import Project
        from .lease import execution_lease
        from .storage import read_json, write_json
        status_file = Path(sys.argv[1]).parent / "status.json"
        async with execution_lease(Project.at(request["project_root"])):
            workflow_status = "UNKNOWN"
            verification_status = "NOT_EVALUATED"
            try:
                result = await execute_request(request)
                if isinstance(result, tuple):
                    code, meta = result
                    workflow_status = meta.get("workflow_status", workflow_status)
                    verification_status = meta.get("verification_status", verification_status)
                else:
                    code = int(result)
                    verification_status = "PASS" if code == 0 else "FAIL"
                    workflow_status = "LEGACY_RETURN"
            except BaseException as exc:
                record = await read_json(status_file, {})
                await write_json(status_file, {**record,
                    "process_status": "FAILED",
                    "workflow_status": workflow_status,
                    "verification_status": "FAIL",
                    "status": "FAIL",
                    "error": f"{type(exc).__name__}: {exc}",
                    "exit_code": 1,
                })
                raise
            record = await read_json(status_file, {})
            await write_json(status_file, {**record,
                "process_exit_code": code,
                "process_status": "COMPLETED" if code == 0 else "FAILED",
                "workflow_status": workflow_status,
                "verification_status": verification_status,
                "status": verification_status,
                "exit_code": code,
            })
            return code
    return asyncio.run(owned())


if __name__ == "__main__":
    raise SystemExit(main())
