"""Deterministic submodule installation, managed files, upgrades and rollback."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import uuid

from .authority import assert_portable_managed_content, neutralize_command_authority
from .context import CORE_RELATIVE, DEFAULT_REPOSITORY, OpsxError, Project
from .process import git, run
from .storage import read_json, write_bytes, write_json
from .worker_runtime import (
    core_poetry_python,
    ensure_core_poetry_runtime,
    interpreter_imports_langgraph,
    is_legacy_managed_runtime_pin,
    is_repository_product_venv,
    resolve_pinned_interpreter,
)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def _render(text: str) -> str:
    """Relocate only known core resources, preserving project-owned locations."""
    prefixes = (
        ".cursor/pipeline/", ".cursor/skills/", "opsx_tools/",
        "tools/cursor_executor/", "tools/langgraph_runtime/", "tools/openspec/",
        "tools/opsx/", "tools/temporal/", "opsx/opsx_runtime/", "opsx/di/",
        "opsx/configs/", "opsx/shared/", "opsx/schemas/", "opsx/contract_prompts/",
        "opsx/adopt_instruction/", "opsx/policies/", "opsx/scripts/",
    )
    expression = "|".join(re.escape(p) for p in sorted(prefixes, key=len, reverse=True))
    text = re.sub(r"(?<![\w/])(" + expression + r")", CORE_RELATIVE + r"/\1", text)
    text = re.sub(r"(?<![\w/])\.cursor/rules/",
                  CORE_RELATIVE + "/.cursor/rules/", text)
    return text


OPSX_CONTEXT_RELATIVE = "opsx/opsx.mdc"


def _opsx_context_source(core: Path) -> Path:
    """Источник @opsx контекста: `opsx/opsx.mdc` рядом с submodule `opsx/core`."""
    source = core.parent / "opsx.mdc"
    if not source.is_file():
        raise OpsxError(
            "OPSX_CONTEXT_MISSING",
            f"Required @opsx context missing beside core checkout: {source}",
        )
    return source


def _managed_payloads(core: Path) -> dict[str, bytes]:
    paths = sorted(
        p for p in (core / ".cursor/commands").glob("*.md") if p.name != "opsx.md"
    )
    result = {}
    for source in paths:
        if not source.is_file():
            raise OpsxError("DISTRIBUTION_INCOMPLETE", f"Missing required asset: {source}")
        relative = source.relative_to(core).as_posix()
        body = neutralize_command_authority(_render(source.read_text(encoding="utf-8")))
        assert_portable_managed_content(relative, body)
        result[relative] = body.encode("utf-8")
    context_source = _opsx_context_source(core)
    context_body = neutralize_command_authority(_render(context_source.read_text(encoding="utf-8")))
    assert_portable_managed_content(OPSX_CONTEXT_RELATIVE, context_body)
    result[OPSX_CONTEXT_RELATIVE] = context_body.encode("utf-8")
    return result


def _validate_core(core: Path) -> None:
    for relative in (
        "pyproject.toml",
        "opsx_cli/cli.py",
        "opsx/opsx_runtime/run_verify_impl.py",
        "opsx_tools/registry/apply_design.schema.json",
    ):
        if not (core / relative).is_file():
            raise OpsxError("CORE_INCOMPLETE", f"Required core file missing: {relative}")
    _opsx_context_source(core)


async def _check_git_project(project: Project) -> None:
    found = await git(project.root, "rev-parse", "--show-toplevel")
    if Path(found.stdout).resolve() != project.root:
        raise OpsxError("PROJECT_ROOT_MISMATCH", "Run at the Git root or pass --project explicitly.")


async def _assert_managed_clean(project: Project, previous: dict) -> None:
    def check() -> None:
        conflicts = []
        for relative, digest in previous.get("managed_files", {}).items():
            path = project.root / relative
            if not path.resolve().is_relative_to(project.root):
                raise OpsxError("MANAGED_PATH_OUTSIDE_PROJECT", f"Managed path escapes project: {relative}")
            if path.is_file() and _digest(path.read_bytes()) != digest:
                conflicts.append(relative)
        if conflicts:
            raise OpsxError("MANAGED_FILE_CONFLICT", "Local edits must be resolved first: " + ", ".join(conflicts))
    await asyncio.to_thread(check)


async def _sync_assets(project: Project, previous: dict) -> dict[str, str]:
    payloads = await asyncio.to_thread(_managed_payloads, project.core)
    def preflight() -> dict[str, bytes | None]:
        before: dict[str, bytes | None] = {}
        for relative, content in payloads.items():
            path = project.root / relative
            if not path.resolve().is_relative_to(project.root):
                raise OpsxError("MANAGED_PATH_OUTSIDE_PROJECT", f"Managed path escapes project: {relative}")
            old = path.read_bytes() if path.is_file() else None
            before[relative] = old
            if old is not None and relative not in previous.get("managed_files", {}) and old != content:
                raise OpsxError("FILE_COLLISION", f"Existing unmanaged file: {relative}")
        return before
    before = await asyncio.to_thread(preflight)
    removed = set(previous.get("managed_files", {})) - set(payloads)
    for relative in removed:
        path = project.root / relative
        before[relative] = await asyncio.to_thread(path.read_bytes) if await asyncio.to_thread(path.is_file) else None
    try:
        for relative, content in payloads.items():
            await write_bytes(project.root / relative, content)
        for relative in removed:
            await asyncio.to_thread((project.root / relative).unlink, missing_ok=True)
    except BaseException:
        for relative, content in before.items():
            path = project.root / relative
            if content is None:
                await asyncio.to_thread(path.unlink, missing_ok=True)
            else:
                await write_bytes(path, content)
        raise
    return {name: _digest(body) for name, body in payloads.items()}


async def _defaults(project: Project) -> None:
    if not await asyncio.to_thread(project.settings.exists):
        await write_json(project.settings, {
            "schema_version": 1, "project_id": uuid.uuid4().hex,
            "name": project.root.name, "provider": "cursor",
            "capabilities": "opsx/project/capabilities.yaml",
            "adapter": {"module": "agents.adapters.cursor_sdk", "extras": ["cursor"]},
            "environment": {}, "workflow": {"timeout_seconds": 2700},
        })
    capabilities = project.root / "opsx/project/capabilities.yaml"
    if not await asyncio.to_thread(capabilities.exists):
        await write_bytes(capabilities, (
            "schema_version: opsx-target-capabilities-v1\n"
            f"target: {json.dumps(project.root.name)}\n"
            "adapter: project-generic\nstatus: NEEDS_ADOPTION\n"
            "product:\n  source_roots: []\n  test_roots: []\n  migration_roots: []\n"
            "quality: {}\n"
        ).encode())
    # These are local runtime data. User .gitignore is deliberately preserved.
    exclude = await git(project.root, "rev-parse", "--git-path", "info/exclude")
    path = Path(exclude.stdout)
    if not path.is_absolute():
        path = project.root / path
    def update_exclude() -> None:
        old = path.read_text(encoding="utf-8") if path.is_file() else ""
        entries = ["/opsx/runs/", "/opsx/logs/", "/opsx/receipts/", "/opsx/tmp/"]
        missing = [entry for entry in entries if entry not in old.splitlines()]
        if missing:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write("\n# OPSX local runtime data\n" + "\n".join(missing) + "\n")
    await asyncio.to_thread(update_exclude)


async def _prepare_runtime(project: Project, sha: str, python: str | None) -> str:
    """Закрепить OPSX runtime за Poetry env ``opsx/core`` (без pip в ``opsx/runs``)."""
    del sha, python  # Poetry pin не привязан к core_sha snapshot.
    from .settings import instance_layer

    settings = instance_layer(await read_json(project.settings, {}))
    if "cursor" in (settings.get("adapter") or {}).get("extras", []):
        py = core_poetry_python(project.core)
        if py is not None:
            await run(
                [
                    str(py),
                    "-c",
                    "import sys; sys.exit('The selected Cursor adapter requires Python 3.12 or later.' if sys.version_info < (3,12) else 0)",
                ],
                cwd=project.root,
                timeout=30,
            )
    return await ensure_core_poetry_runtime(project)


async def install(
    project: Project, *, repository: str = DEFAULT_REPOSITORY, ref: str | None = None,
    no_runtime: bool = False, python: str | None = None,
    migrate_legacy: bool = False, migration_dry_run: bool = False, migration_apply: bool = False,
) -> dict:
    from .lease import execution_lease
    from .migration import build_plan, legacy_present, migrate_legacy as run_legacy, retire_legacy_tree
    async with execution_lease(project):
        migration_report = None
        if migrate_legacy or migration_dry_run or migration_apply:
            migration_report = await run_legacy(
                project, core=project.core if await asyncio.to_thread(project.core.is_dir) else None,
                dry_run=migration_dry_run or (migrate_legacy and not migration_apply), apply=migration_apply,
            )
            if migration_dry_run or (migrate_legacy and not migration_apply):
                return {"status": "MIGRATION_PLAN", **migration_report}
            if migration_apply:
                plan = build_plan(project, core=project.core if await asyncio.to_thread(project.core.is_dir) else None)
                if plan.get("status") == "BLOCKED":
                    raise OpsxError("MIGRATION_BLOCKED", "Resolve migration manifest before install.")
                from .migration import stage_proven_command_replacements
                await stage_proven_command_replacements(project, plan)
        result = await _install_locked(project, repository=repository, ref=ref,
                                       no_runtime=no_runtime, python=python)
        if migration_apply and result.get("managed_files"):
            retired = await retire_legacy_tree(project, installation_managed=result["managed_files"])
            result["legacy_retirement"] = retired
        if migration_report:
            result["migration"] = migration_report
        return result


async def _install_locked(
    project: Project, *, repository: str = DEFAULT_REPOSITORY, ref: str | None = None,
    no_runtime: bool = False, python: str | None = None,
) -> dict:
    await _check_git_project(project)
    existing = await read_json(project.installation)
    if existing:
        await git(project.root, "submodule", "update", "--init", "--", CORE_RELATIVE)
        await _assert_managed_clean(project, existing)
        await asyncio.to_thread(_validate_core, project.core)
        await _defaults(project)
        if not no_runtime:
            runtime_pin = existing.get("runtime_python")
            pin_text = (
                str(runtime_pin).strip()
                if isinstance(runtime_pin, str) and runtime_pin.strip()
                else ""
            )
            pin_path = (
                await asyncio.to_thread(resolve_pinned_interpreter, project, pin_text)
                if pin_text
                else None
            )
            poetry_py = await asyncio.to_thread(core_poetry_python, project.core)
            needs_prepare = (
                not pin_text
                or is_legacy_managed_runtime_pin(pin_text)
                or pin_path is None
                or await asyncio.to_thread(
                    is_repository_product_venv, project, pin_path
                )
                or not await asyncio.to_thread(
                    interpreter_imports_langgraph, str(pin_path)
                )
                or poetry_py is None
                or os.path.normcase(str(pin_path))
                != os.path.normcase(str(poetry_py))
            )
            if needs_prepare:
                interpreter = await _prepare_runtime(project, existing["core_sha"], python)
                existing = {**existing, "runtime_python": interpreter}
                await write_json(project.installation, existing)
        return {"status": "ALREADY_INSTALLED", **existing}
    pending_path = project.runs / "installation-pending.json"
    pending = await read_json(pending_path)
    occupied = await asyncio.to_thread(project.core.exists)
    if occupied and (not pending or pending.get("repository") != repository):
        raise OpsxError("CORE_PATH_OCCUPIED", f"Destination exists: {project.core}")
    await write_json(pending_path, {"repository": repository, "ref": ref, "core_path": CORE_RELATIVE})
    added = False
    try:
        if not occupied:
            await git(project.root, "submodule", "add", "--", repository, CORE_RELATIVE)
        added = True
        if ref:
            await git(project.core, "checkout", "--detach", ref)
        await asyncio.to_thread(_validate_core, project.core)
        sha = (await git(project.core, "rev-parse", "HEAD")).stdout
        managed = await _sync_assets(project, {})
        await _defaults(project)
        interpreter = None if no_runtime else await _prepare_runtime(project, sha, python)
        metadata = {"schema_version": 1, "repository": repository, "core_path": CORE_RELATIVE,
                    "core_sha": sha, "ref": ref, "runtime_python": interpreter, "managed_files": managed}
        await git(project.root, "add", "--", ".gitmodules", CORE_RELATIVE)
        await write_json(project.installation, metadata)
        await asyncio.to_thread(pending_path.unlink, missing_ok=True)
        return {"status": "INSTALLED", **metadata}
    except BaseException:
        # Retain a failed checkout for diagnosis and resume; never remove user data.
        if added:
            await write_json(project.runs / "installation-error.json",
                             {"code": "INSTALL_INCOMPLETE", "core_path": CORE_RELATIVE,
                              "next_action": "Resolve the error, then repeat opsx install with the same repository."})
        raise


async def upgrade(project: Project, *, ref: str | None = None, no_runtime: bool = False,
                  python: str | None = None) -> dict:
    from .lease import execution_lease
    async with execution_lease(project):
        return await _upgrade_locked(project, ref=ref, no_runtime=no_runtime, python=python)


async def _upgrade_locked(project: Project, *, ref: str | None = None, no_runtime: bool = False,
                  python: str | None = None) -> dict:
    from .service import panel_status
    previous = await read_json(project.installation)
    if not previous:
        raise OpsxError("NOT_INSTALLED", "Run opsx install first.")
    if (await panel_status(project)).get("running"):
        raise OpsxError("PANEL_RUNNING", "Stop the local panel before upgrading: opsx stop")
    await _assert_managed_clean(project, previous)
    if (await git(project.core, "status", "--porcelain")).stdout:
        raise OpsxError("CORE_DIRTY", "Commit or preserve changes inside the core before upgrading.")
    old_sha = (await git(project.core, "rev-parse", "HEAD")).stdout
    before = await asyncio.to_thread(
        lambda: {p: (project.root / p).read_bytes() if (project.root / p).is_file() else None
                 for p in previous.get("managed_files", {})})
    try:
        await git(project.core, "fetch", "origin", "--tags")
        selected = ref or "origin/HEAD"
        await git(project.core, "checkout", "--detach", selected)
        await asyncio.to_thread(_validate_core, project.core)
        incoming = await asyncio.to_thread(_managed_payloads, project.core)
        for relative in set(incoming) - set(before):
            path = project.root / relative
            before[relative] = await asyncio.to_thread(path.read_bytes) if await asyncio.to_thread(path.is_file) else None
        sha = (await git(project.core, "rev-parse", "HEAD")).stdout
        interpreter = None if no_runtime else await _prepare_runtime(project, sha, python)
        managed = await _sync_assets(project, previous)
        metadata = {**previous, "core_sha": sha, "ref": ref, "runtime_python": interpreter,
                    "managed_files": managed}
        await git(project.root, "add", "--", CORE_RELATIVE)
        await write_json(project.installation, metadata)
        return {"status": "UPGRADED", **metadata}
    except BaseException:
        await git(project.core, "checkout", "--detach", old_sha)
        for relative, body in before.items():
            if body is not None:
                await write_bytes(project.root / relative, body)
            else:
                await asyncio.to_thread((project.root / relative).unlink, missing_ok=True)
        await git(project.root, "add", "--", CORE_RELATIVE)
        await write_json(project.installation, previous)
        raise


async def uninstall(project: Project) -> dict:
    from .lease import execution_lease
    async with execution_lease(project):
        return await _uninstall_locked(project)


async def _uninstall_locked(project: Project) -> dict:
    from .service import panel_status
    metadata = await read_json(project.installation)
    if not metadata:
        return {"status": "NOT_INSTALLED"}
    if (await panel_status(project)).get("running"):
        raise OpsxError("PANEL_RUNNING", "Stop the local panel first: opsx stop")
    await _assert_managed_clean(project, metadata)
    if (await git(project.core, "status", "--porcelain")).stdout:
        raise OpsxError("CORE_DIRTY", "Preserve core edits before uninstalling.")
    for relative in metadata.get("managed_files", {}):
        await asyncio.to_thread((project.root / relative).unlink, missing_ok=True)
    await git(project.root, "submodule", "deinit", "--", CORE_RELATIVE)
    await git(project.root, "rm", "--", CORE_RELATIVE)
    await asyncio.to_thread(project.installation.unlink)
    return {"status": "UNINSTALLED", "preserved": ["opsx/project", "opsx/receipts", "opsx/runs", "opsx/logs", "openspec"]}


async def doctor(project: Project) -> dict:
    metadata = await read_json(project.installation)
    checks = []
    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})
    add("installation", bool(metadata))
    if metadata:
        result = await git(project.core, "rev-parse", "HEAD", check=False)
        add("pinned_core", result.code == 0 and result.stdout == metadata["core_sha"], result.stdout)
        add("project_settings", await asyncio.to_thread(project.settings.is_file))
        try:
            await _assert_managed_clean(project, metadata)
            missing = await asyncio.to_thread(lambda: [p for p in metadata["managed_files"] if not (project.root / p).is_file()])
            add("cursor_integration", not missing, ", ".join(missing))
        except OpsxError as exc:
            add("cursor_integration", False, str(exc))
        interpreter = metadata.get("runtime_python")
        pinned = (
            await asyncio.to_thread(
                resolve_pinned_interpreter, project, str(interpreter)
            )
            if interpreter
            else None
        )
        add(
            "runtime_environment",
            pinned is not None,
            str(pinned or interpreter or ""),
        )
        from .worker_runtime import resolve_worker_interpreter

        try:
            worker_python = await asyncio.to_thread(
                resolve_worker_interpreter, project, metadata
            )
            add("worker_langgraph", True, worker_python)
            from .storage import read_json as _read_json
            from .worker_runtime import (
                interpreter_imports_cursor_sdk,
                poetry_cursor_extra_requested,
            )

            settings = await _read_json(project.settings, {})
            if await asyncio.to_thread(
                poetry_cursor_extra_requested, project, settings
            ):
                add(
                    "worker_cursor_sdk",
                    await asyncio.to_thread(
                        interpreter_imports_cursor_sdk, worker_python
                    ),
                    worker_python,
                )
        except OpsxError as exc:
            add("worker_langgraph", False, str(exc))
        from .service import resolve_panel_interpreter

        try:
            panel_python = await resolve_panel_interpreter(project, metadata or {})
            add("panel_web_deps", True, panel_python)
        except OpsxError as exc:
            add("panel_web_deps", False, str(exc))
        settings = await read_json(project.settings, {})
        capability = project.root / settings.get("capabilities", "opsx/project/capabilities.yaml")
        add("capability_profile", await asyncio.to_thread(capability.is_file))
        if await asyncio.to_thread(capability.is_file):
            body = await asyncio.to_thread(capability.read_text, encoding="utf-8")
            add("project_adoption", "status: NEEDS_ADOPTION" not in body,
                "Resolve this project's capabilities through /opsx-adopt.")
        add("node", bool(shutil.which("node")), "Required for existing machine gates")
        add(
            "manual_opsx_context",
            await asyncio.to_thread((project.root / OPSX_CONTEXT_RELATIVE).is_file),
        )
    return {"status": "PASS" if checks and all(c["ok"] for c in checks) else "NOT_READY", "checks": checks}
