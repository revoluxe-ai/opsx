"""Process entrypoint for existing runtime use cases, independent of CLI/HTTP."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys


async def execute_request(request: dict) -> int | tuple[int, dict]:
    """Выполнить request.json; workflow, repair, supervise fix/commit/rerun_checks."""
    project = Path(request["project_root"]).resolve()
    core = Path(request["core_root"]).resolve()
    os.environ["OPSX_PROJECT_ROOT"] = str(project)
    os.environ["OPSX_CORE_ROOT"] = str(core)
    settings_file = project / "opsx/project/project.json"
    settings_text = await asyncio.to_thread(settings_file.read_text, encoding="utf-8")
    from .settings import instance_layer

    settings = instance_layer(json.loads(settings_text))
    adapter_module = (settings.get("adapter") or {}).get("module")
    if adapter_module:
        os.environ["OPSX_ADAPTER_MODULE"] = str(adapter_module)
    capability_file = project / "opsx/project/capabilities.yaml"
    if capability_file.is_file():
        import yaml

        capability_text = await asyncio.to_thread(capability_file.read_text, encoding="utf-8")
        capabilities = yaml.safe_load(capability_text) or {}
        if capabilities.get("status") == "NEEDS_ADOPTION":
            raise ValueError(
                "PROJECT_ADOPTION_REQUIRED: resolve this project's capabilities before execution"
            )
        source_roots = (capabilities.get("product") or {}).get("source_roots") or []
        os.environ["OPSX_PRODUCT_SOURCE_ROOTS_JSON"] = json.dumps(source_roots)
    for path in (
        core,
        core / "opsx/opsx_runtime",
        core / "opsx/configs",
        core / ".cursor/pipeline",
        core / "tools/cursor_executor",
    ):
        sys.path.insert(0, str(path))
    from opsx.project_context import bind_project_context, reset_project_context

    token = bind_project_context(project, core)
    try:
        mode = request["mode"]
        if mode == "workflow":
            from run_verify_impl import execute
            from workflows.verify.cli_outcome import (
                raise_if_implementation_lane_terminal,
                raise_unless_security_pass,
            )

            outcome = await execute(
                request["change_id"],
                repo_root=project,
                timeout_seconds=request["timeout_seconds"],
            )
            raise_if_implementation_lane_terminal(outcome, change_id=request["change_id"])
            raise_unless_security_pass(outcome, change_id=request["change_id"])
            print("SECURITY_COMPLETE: PASS", flush=True)
            return 0, {
                "workflow_status": "WORKFLOW_TERMINAL",
                "verification_status": "PASS",
            }
        if mode == "supervise_qualify":
            core_str = str(core)
            if core_str not in sys.path:
                sys.path.insert(0, core_str)
            from agents.process_repair.qualifier_runner import run_process_supervisor_qualify

            attempt_dir = Path(request["attempt_dir"])
            report = run_process_supervisor_qualify(request)
            await asyncio.to_thread(
                (attempt_dir / "qualify_result.json").write_text,
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return 0, {
                "workflow_status": "SUPERVISE_QUALIFY",
                "verification_status": "NOT_EVALUATED",
            }
        if mode == "supervise_fix":
            from agents.process_repair.fix_runner import run_process_supervisor_fix

            attempt_dir = Path(request["attempt_dir"])
            report = await run_process_supervisor_fix(request)
            await asyncio.to_thread(
                (attempt_dir / "fix_result.json").write_text,
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            verdict = str(report.get("verdict") or "").upper()
            code = 0 if verdict == "FIXED" else 1
            return code, {
                "workflow_status": f"SUPERVISE_FIX_{verdict or 'UNKNOWN'}",
                "verification_status": "NOT_EVALUATED",
            }
        if mode == "supervise_commit":
            from agents.process_repair.commit_runner import commit_journal_entry

            journal_path = Path(request["journal_path"])
            attempt_dir = Path(request["attempt_dir"])
            outcome = await asyncio.to_thread(
                commit_journal_entry,
                project_root=project,
                core_root=core,
                journal_path=journal_path,
                entry_id=str(request["entry_id"]),
            )
            payload = outcome.model_dump()
            await asyncio.to_thread(
                (attempt_dir / "commit_result.json").write_text,
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            code = 0 if outcome.status == "COMMITTED" else 1
            return code, {
                "workflow_status": f"SUPERVISE_COMMIT_{outcome.status}",
                "verification_status": "NOT_EVALUATED",
            }
        if mode == "supervise_rerun_checks":
            # Перезапуск named pytest из свежего worker-процесса (панель может держать старый opsx_cli).
            from opsx_cli.context import Project
            from opsx_cli.service import RunService

            attempt_dir = Path(request["attempt_dir"])
            project_root = Project.at(request["project_root"])
            service = RunService(project_root)
            fix_report = request.get("fix_report") or {}
            timeout = float(request.get("timeout_seconds") or 600.0)
            checks = await service.rerun_fix_checks(fix_report, timeout_seconds=timeout)
            await asyncio.to_thread(
                (attempt_dir / "rerun_checks_result.json").write_text,
                json.dumps({"checks_run": checks}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            failed = any(int(row.get("exit_code") or 1) != 0 for row in checks)
            return (1 if failed else 0), {
                "workflow_status": "SUPERVISE_RERUN_CHECKS",
                "verification_status": "NOT_EVALUATED",
            }
        if mode != "repair":
            raise ValueError("REQUEST_MODE_UNSUPPORTED")
        ui = core / "opsx/opsx_runtime/ui/named_bug_repair"
        sys.path.insert(0, str(ui))
        from bootstrap import prepare_named_bug_repair_runtime
        from agents.adapters.bridge_runtime_port import open_bridge_runtime
        from intake import run_owner_named_bug_repair

        prepare_named_bug_repair_runtime(repo_root=project)
        async with open_bridge_runtime(project) as bridge:
            outcome = await run_owner_named_bug_repair(
                repository_path=project,
                bug_text=request["bug"],
                change_id=request["change_id"],
                timeout_seconds=request["timeout_seconds"],
                bridge_runtime=bridge,
                on_log_line=lambda line: print(line, flush=True),
                on_progress=lambda payload: print(json.dumps({"opsx_event": payload}), flush=True),
            )
        summary = outcome.get("owner_named_check_repair_summary") or {}
        status = str(
            summary.get("status") or outcome.get("owner_named_check_repair_status") or ""
        ).upper()
        print(json.dumps({"repair_result": summary}, ensure_ascii=False), flush=True)
        ok = status in {
            "PASS",
            "FIXED",
            "COMPLETED",
            "REPAIR_COMPLETE",
            "COMPLETE_NO_CLASS_LESSON",
            "COMPLETE_CLASS_LESSON",
        }
        verify = "PASS" if status in {"PASS", "FIXED"} else ("NOT_READY" if ok else "FAIL")
        return (0 if ok else 1), {
            "workflow_status": status or "REPAIR_UNKNOWN",
            "verification_status": verify,
        }
    finally:
        reset_project_context(token)


async def _publish_attempt(
    status_file: Path,
    outcome_file: Path,
    outcome: dict,
    *,
    supervised: bool,
) -> None:
    """Записать исход попытки, не закрывая страницу, пока идёт супервизор.

    Без супервизора исход пишется в ``status.json``, как раньше.
    Под супервизором он лежит рядом с request, а статус сайта остаётся ``RUNNING``.
    """
    from .site_status import keep_site_running
    from .storage import read_json, write_json

    if not supervised:
        record = await read_json(status_file, {})
        await write_json(status_file, {**record, **outcome})
        return
    await write_json(outcome_file, outcome)
    current = await read_json(status_file, {})
    visible = keep_site_running(current)
    visible["last_attempt"] = {
        "verification_status": outcome.get("verification_status"),
        "workflow_status": outcome.get("workflow_status"),
        "exit_code": outcome.get("exit_code"),
    }
    await write_json(status_file, visible)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("A request file is required.")
    request_path = Path(sys.argv[1])
    request = json.loads(request_path.read_text(encoding="utf-8"))

    async def owned() -> int:
        from .context import Project
        from .lease import execution_lease
        from .storage import read_json, write_json

        status_file = request_path.parent / "status.json"
        outcome_file = request_path.with_name(request_path.stem + ".outcome.json")
        supervised = bool(request.get("supervised_run_id"))
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
                outcome = {
                    "process_status": "FAILED",
                    "workflow_status": workflow_status,
                    "verification_status": "FAIL",
                    "status": "FAIL",
                    "error": f"{type(exc).__name__}: {exc}",
                    "exit_code": 1,
                }
                await _publish_attempt(status_file, outcome_file, outcome, supervised=supervised)
                raise
            outcome = {
                "process_exit_code": code,
                "process_status": "COMPLETED" if code == 0 else "FAILED",
                "workflow_status": workflow_status,
                "verification_status": verification_status,
                "status": verification_status,
                "exit_code": code,
            }
            await _publish_attempt(status_file, outcome_file, outcome, supervised=supervised)
            return code

    return asyncio.run(owned())


if __name__ == "__main__":
    raise SystemExit(main())