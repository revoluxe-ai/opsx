"""Детерминированный ``run_opsx_pytest`` для META fix-hop супервизора."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agents.adapters.agent_capability_options import AgentCustomToolSpec
from agents.process_repair.meta_surface import PROCESS_SUPERVISOR_FIX_KEY

RUN_OPSX_PYTEST_TOOL_NAME = "run_opsx_pytest"


def _resolve_poetry(core_root: Path) -> list[str]:
    poetry = shutil.which("poetry")
    if poetry:
        return [poetry, "run", "python", "-m", "pytest"]
    return [shutil.which("python") or "python", "-m", "pytest"]


def resolve_opsx_runtime_root(core_root: Path) -> Path:
    """Корень пакета ``agents`` / ``workflows``.

    Установленное ядро лежит в ``<root>/opsx/opsx_runtime``. Checkout, где
    ядро — submodule ``opsx/core``, держит тот же пакет в
    ``<root>/opsx/core/opsx/opsx_runtime``.
    """
    flat = core_root / "opsx" / "opsx_runtime"
    nested = core_root / "opsx" / "core" / "opsx" / "opsx_runtime"
    if flat.is_dir():
        return flat
    if nested.is_dir():
        return nested
    return flat


def build_run_opsx_pytest_custom_tool(*, core_root: Path) -> Mapping[str, AgentCustomToolSpec]:
    """MCP tool: один pytest node под ``opsx/opsx_runtime``."""

    async def _run(args: Mapping[str, Any] | str, _ctx: Any = None) -> dict[str, Any]:
        # MCP host calls execute(args, ctx). A bare node id stays valid for tests.
        if isinstance(args, str):
            node_id = args
        elif isinstance(args, Mapping):
            node_id = str(args.get("node_id") or "")
        else:
            return {"ok": False, "error": "INVALID_ARGS", "field": "node_id"}
        normalized = node_id.strip().replace("\\", "/")
        if normalized.startswith("opsx/opsx_runtime/"):
            rel = normalized.removeprefix("opsx/opsx_runtime/")
        elif normalized.startswith("tests/"):
            rel = normalized
        else:
            return {"ok": False, "error": "node_id must start with opsx/opsx_runtime/ or tests/"}
        runtime_root = resolve_opsx_runtime_root(core_root)
        target = runtime_root / rel
        from agents.agent_tools.deterministic.pytest_tool import (
            product_pytest_subprocess_env,
            resolve_product_pytest_python,
            resolve_repository_venv_python,
        )

        env = product_pytest_subprocess_env()
        prior = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            str(runtime_root) if not prior else str(runtime_root) + os.pathsep + prior
        )
        project_python = resolve_product_pytest_python(core_root, env)
        if project_python is None:
            project_python = resolve_repository_venv_python(core_root)
        if project_python is not None:
            argv = [str(project_python), "-m", "pytest", str(target), "-q", "--tb=short"]
        else:
            argv = [*_resolve_poetry(core_root), str(target), "-q", "--tb=short"]
        completed = subprocess.run(
            argv,
            cwd=core_root,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        return {
            "ok": completed.returncode == 0,
            "exit_code": completed.returncode,
            "stdout_tail": (completed.stdout or "")[-2500:],
            "stderr_tail": (completed.stderr or "")[-2500:],
        }

    return {
        RUN_OPSX_PYTEST_TOOL_NAME: AgentCustomToolSpec(
            description="Run one pytest node under opsx/opsx_runtime (no shell).",
            input_schema={
                "type": "object",
                "properties": {"node_id": {"type": "string"}},
                "required": ["node_id"],
            },
            execute=_run,
        )
    }


PROCESS_SUPERVISOR_AGENT_KEYS = frozenset({PROCESS_SUPERVISOR_FIX_KEY})