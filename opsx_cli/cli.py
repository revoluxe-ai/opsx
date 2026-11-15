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
    for name in ("install", "upgrade", "doctor", "uninstall", "start", "stop", "status", "version", "run", "repair"):
        command = commands.add_parser(name)
        command.add_argument("--project", default=".", help="Project Git root")
        command.add_argument("--json", action="store_true", help="Machine-readable result")
        if name in ("install", "upgrade"):
            command.add_argument("--ref", help="Exact Git tag or commit")
            command.add_argument("--no-runtime", action="store_true", help="Explicitly omit the execution environment")
            command.add_argument("--python", help="Python executable for the project execution environment")
        if name == "install":
            command.add_argument("--repository", default=DEFAULT_REPOSITORY)
        if name == "start":
            command.add_argument("--port", type=int, default=8765)
            command.add_argument("--no-browser", action="store_true")
        if name in ("run", "repair"):
            command.add_argument("change", nargs="?" if name == "repair" else None, default="owner-named" if name == "repair" else None)
            command.add_argument("--timeout", type=float, default=2700)
        if name == "repair":
            command.add_argument("--bug", required=True, help="Description of the concrete defect")
    return root


async def dispatch(args: argparse.Namespace) -> tuple[dict, int]:
    from .installer import doctor, install, uninstall, upgrade
    from .service import RunService, panel_status, start_panel, stop_panel
    from .storage import read_json
    project = Project.at(args.project)
    match args.command:
        case "install":
            result = await install(project, repository=args.repository, ref=args.ref,
                                   no_runtime=args.no_runtime, python=args.python)
        case "upgrade":
            result = await upgrade(project, ref=args.ref, no_runtime=args.no_runtime, python=args.python)
        case "doctor":
            result = await doctor(project)
            return result, 0 if result["status"] == "PASS" else 1
        case "uninstall":
            result = await uninstall(project)
        case "start":
            result = await start_panel(project, port=args.port, browser=not args.no_browser)
        case "stop":
            result = await stop_panel(project)
        case "status":
            result = {**(await panel_status(project)), "runs": await RunService(project).history()}
        case "version":
            installed = await read_json(project.installation, {})
            result = {"cli": __version__, "core_sha": installed.get("core_sha"), "ref": installed.get("ref")}
        case "run" | "repair":
            result = await RunService(project).execute(
                mode="workflow" if args.command == "run" else "repair",
                change=args.change, bug=getattr(args, "bug", ""), timeout=args.timeout, echo=True)
            return result, result["exit_code"]
        case _:
            raise OpsxError("COMMAND_UNKNOWN", args.command)
    return result, 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
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
