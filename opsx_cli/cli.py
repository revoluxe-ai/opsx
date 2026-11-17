"""Public OPSX CLI. Installation does not import model adapters."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from . import __version__
from .context import DEFAULT_REPOSITORY, OpsxError, Project


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="opsx", description="OPSX project workflows and local control panel")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("install", "upgrade", "doctor", "uninstall", "start", "stop", "status", "version", "run", "repair",
                 "config", "project"):
        command = commands.add_parser(name)
        command.add_argument("--project", default=".", help="Project Git root")
        command.add_argument("--json", action="store_true", help="Machine-readable result")
        if name in ("install", "upgrade"):
            command.add_argument("--ref", help="Exact Git tag or commit")
            command.add_argument("--no-runtime", action="store_true", help="Explicitly omit the execution environment")
            command.add_argument("--python", help="Python executable for the project execution environment")
        if name == "install":
            command.add_argument("--repository", default=DEFAULT_REPOSITORY)
            command.add_argument("--migrate-legacy", action="store_true",
                                 help="Plan or run legacy OPSX cutover before installation")
            command.add_argument("--migration-dry-run", action="store_true",
                                 help="Read-only legacy migration plan")
            command.add_argument("--migration-apply", action="store_true",
                                 help="Apply migration actions then install")
        if name == "config":
            command.add_argument("config_action", choices=["show", "validate"], nargs="?")
            command.add_argument("--section", choices=["installation", "instance"])
        if name == "project":
            command.add_argument("project_action", choices=["show", "validate"], nargs="?")
        if name == "start":
            command.add_argument("--port", type=int, default=8765)
            command.add_argument("--no-browser", action="store_true")
        if name == "run":
            command.add_argument("change")
            command.add_argument("--workflow-timeout-min", type=float, default=45)
            command.add_argument("--supervisor-timeout-min", type=float, default=180)
            command.add_argument("--max-attempts", type=int, default=5)
        if name == "repair":
            command.add_argument("change", nargs="?", default="owner-named")
            command.add_argument("--timeout", type=float, default=2700)
            command.add_argument("--bug", required=True, help="Description of the concrete defect")
    return root


async def dispatch(args: argparse.Namespace) -> tuple[dict, int]:
    from .config_commands import config_show, config_validate
    from .installer import doctor, install, uninstall, upgrade
    from .project_commands import project_show, project_validate
    from .service import RunService, panel_status, stop_panel
    from .storage import read_json
    project = Project.at(args.project)
    match args.command:
        case "install":
            dry = args.migration_dry_run or (args.migrate_legacy and not args.migration_apply)
            result = await install(
                project, repository=args.repository, ref=args.ref,
                no_runtime=args.no_runtime, python=args.python,
                migrate_legacy=args.migrate_legacy or args.migration_apply,
                migration_dry_run=dry,
                migration_apply=args.migration_apply,
            )
        case "upgrade":
            result = await upgrade(project, ref=args.ref, no_runtime=args.no_runtime, python=args.python)
        case "doctor":
            result = await doctor(project)
            return result, 0 if result["status"] == "PASS" else 1
        case "uninstall":
            result = await uninstall(project)
        case "start":
            from .service import foreground_start
            await foreground_start(project, port=args.port, browser=not args.no_browser)
            return {"status": "STOPPED"}, 0
        case "stop":
            result = await stop_panel(project)
        case "status":
            result = {**(await panel_status(project)), "runs": await RunService(project).history()}
        case "version":
            installed = await read_json(project.installation, {})
            result = {"cli": __version__, "core_sha": installed.get("core_sha"), "ref": installed.get("ref")}
        case "run":
            result = await RunService(project).execute(
                mode="workflow",
                change=args.change,
                workflow_timeout_minutes=args.workflow_timeout_min,
                supervisor_timeout_minutes=args.supervisor_timeout_min,
                max_attempts=args.max_attempts,
                echo=True,
            )
            code = result.get("exit_code", 1)
            verify = result.get("verification_status")
            if verify in {"FAIL", "NOT_READY", "NOT_EVALUATED"}:
                code = 1 if verify != "PASS" else 0
            return result, code
        case "repair":
            result = await RunService(project).execute(
                mode="repair",
                change=args.change,
                bug=args.bug,
                timeout=args.timeout,
                echo=True,
            )
            code = result.get("exit_code", 1)
            verify = result.get("verification_status")
            if verify in {"FAIL", "NOT_READY", "NOT_EVALUATED"}:
                code = 1 if verify != "PASS" else 0
            return result, code
        case "config":
            action = args.config_action or "show"
            result = await (config_validate if action == "validate" else config_show)(
                project, section=getattr(args, "section", None))
            if action == "validate":
                return result, 0 if result.get("status") == "PASS" else 1
        case "project":
            action = args.project_action or "show"
            result = await (project_validate if action == "validate" else project_show)(project)
            if action == "validate":
                return result, 0 if result.get("status") == "PASS" else 1
        case _:
            raise OpsxError("COMMAND_UNKNOWN", args.command)
    return result, 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "start" and not args.json:
        from .context import Project
        from .service import foreground_start
        try:
            asyncio.run(foreground_start(
                Project.at(args.project), port=args.port, browser=not args.no_browser,
            ))
        except OpsxError as exc:
            print(json.dumps({"status": "ERROR", "code": exc.code, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            print("Stopped.", flush=True)
            return 130
        return 0
    try:
        result, code = asyncio.run(dispatch(args))
    except OpsxError as exc:
        print(json.dumps({"status": "ERROR", "code": exc.code, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted. Executor outcome must be reconciled before another run.", file=sys.stderr)
        return 130
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code
