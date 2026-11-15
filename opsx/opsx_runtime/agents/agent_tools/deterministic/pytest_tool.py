"""Детерминированный MCP ``run_pytest``: один node id под ``tests/``, без shell у модели."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agents.adapters.agent_capability_options import AgentCustomToolSpec
from workflows.implementation.node_subprocess import run_subprocess_utf8

RUN_PYTEST_TOOL_NAME = "run_pytest"
_OUTPUT_TAIL_CHARS = 3_500
_DEFAULT_TIMEOUT_SECONDS = 600.0
_SHELL_METACHAR_RE = re.compile(r"[;&|`$<>\r\n\0\\\"]")


def _resolve_unique_tests_basename(repo_root: Path, basename: str) -> str | None:
    """Единственный ``tests/**/<basename>`` в репо или ``None`` при неоднозначности."""
    name = basename.strip().replace("\\", "/")
    if not name or "/" in name or ".." in name or not name.endswith(".py"):
        return None
    tests_root = repo_root.resolve() / "tests"
    if not tests_root.is_dir():
        return None
    hits: list[str] = []
    for path in tests_root.rglob(name):
        if path.is_file() and path.name == name:
            rel = path.relative_to(repo_root.resolve()).as_posix()
            hits.append(rel)
    unique = list(dict.fromkeys(hits))
    if len(unique) == 1:
        return unique[0]
    return None


def validate_repair_hop_pytest_node_id(
    node_id: str,
    *,
    repo_root: Path | None = None,
) -> str:
    """Проверить и нормализовать pytest target под ``tests/`` (node id или файл).

    Bare basename (например ``test_foo.py``) резолвится в единственный путь под
    ``tests/``; shell-команды для pytest запрещены (§27.0.1).

    Raises:
        ValueError: пустой id, shell-метасимволы, вне ``tests/``, неоднозначный basename.
    """
    raw = str(node_id or "").strip()
    if not raw:
        msg = "pytest node_id must not be empty"
        raise ValueError(msg)
    if _SHELL_METACHAR_RE.search(raw):
        msg = "pytest node_id contains forbidden shell metacharacters"
        raise ValueError(msg)
    if any(sep in raw for sep in (" ", ",", "\t")):
        msg = "pytest node_id must be a single node id"
        raise ValueError(msg)
    normalized = raw.replace("\\", "/")
    if not normalized.startswith("tests/"):
        if repo_root is not None and "/" not in normalized and normalized.endswith(".py"):
            resolved = _resolve_unique_tests_basename(repo_root, normalized)
            if resolved is None:
                msg = (
                    "pytest node_id must start with tests/ "
                    "or be a unique test module basename under tests/"
                )
                raise ValueError(msg)
            normalized = resolved
        else:
            msg = "pytest node_id must start with tests/"
            raise ValueError(msg)
    file_part = normalized.split("::", 1)[0]
    if "/.." in file_part or file_part.startswith(".."):
        msg = "pytest node_id path must not traverse parent directories"
        raise ValueError(msg)
    if not file_part.endswith(".py"):
        msg = f"pytest node_id invalid file part: {file_part!r}"
        raise ValueError(msg)
    if "::" in normalized:
        file_part, test_part = normalized.split("::", 1)
        if not test_part.strip():
            msg = f"pytest node_id invalid shape: {normalized!r}"
            raise ValueError(msg)
    return normalized


def _resolve_poetry(repo_root: Path) -> list[str]:
    poetry = shutil.which("poetry")
    if poetry:
        return [poetry]
    return ["python", "-m", "poetry"]


def build_repair_hop_pytest_argv(repo_root: Path, node_id: str) -> list[str]:
    """Собрать argv subprocess для одного product pytest node id."""
    repo_root = repo_root.resolve()
    normalized = validate_repair_hop_pytest_node_id(node_id, repo_root=repo_root)
    prefix = _resolve_poetry(repo_root)
    return [
        *prefix,
        "run",
        "python",
        "-m",
        "pytest",
        normalized,
        "-q",
        "--tb=short",
    ]


def _tail_output(stdout: str, stderr: str, *, limit: int = _OUTPUT_TAIL_CHARS) -> str:
    merged = "\n".join(part for part in (stdout.strip(), stderr.strip()) if part)
    if len(merged) <= limit:
        return merged
    return f"… [truncated; showing last {limit} chars]\n{merged[-limit:]}"


def run_repair_hop_pytest_sync(
    repo_root: Path,
    node_id: str,
    *,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Выполнить pytest для одного node id; вернуть exit code и хвост вывода."""
    repo_root = repo_root.resolve()
    normalized = validate_repair_hop_pytest_node_id(node_id, repo_root=repo_root)
    cmd = build_repair_hop_pytest_argv(repo_root, normalized)
    completed = run_subprocess_utf8(
        cmd,
        cwd=repo_root,
        timeout=timeout_seconds,
    )
    tail = _tail_output(completed.stdout or "", completed.stderr or "")
    exit_code = int(completed.returncode)
    return {
        "ok": exit_code == 0,
        "exit_code": exit_code,
        "node_id": normalized,
        "tail": tail,
        "timed_out": False,
    }


def _mcp_payload(**fields: Any) -> dict[str, Any]:
    text = json.dumps(fields, ensure_ascii=False, indent=2)
    return {
        "content": [{"type": "text", "text": text}],
        **fields,
    }


class RepairHopRunPytestRuntime:
    """Исполнитель custom MCP ``run_pytest`` для repair hops."""

    def __init__(self, *, repo_root: Path) -> None:
        self._repo_root = repo_root.resolve()

    async def run_pytest(self, args: Mapping[str, Any], _ctx: Any) -> dict[str, Any]:
        """Запустить pytest по одному ``node_id`` из схемы tool."""
        if not isinstance(args, Mapping):
            return _mcp_payload(ok=False, code="INVALID_ARGS")
        node_raw = args.get("node_id")
        if node_raw is None:
            return _mcp_payload(ok=False, code="INVALID_ARGS", field="node_id")
        try:
            normalized = validate_repair_hop_pytest_node_id(
                str(node_raw),
                repo_root=self._repo_root,
            )
        except ValueError as exc:
            return _mcp_payload(ok=False, code="INVALID_NODE_ID", error=str(exc))
        try:
            result = await asyncio.to_thread(
                run_repair_hop_pytest_sync,
                self._repo_root,
                normalized,
            )
        except Exception as exc:
            return _mcp_payload(
                ok=False,
                code="PYTEST_RUN_FAILED",
                node_id=normalized,
                error=f"{type(exc).__name__}: {exc}",
            )
        return _mcp_payload(**result)


_RUN_PYTEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["node_id"],
    "properties": {
        "node_id": {
            "type": "string",
            "minLength": 1,
            "description": "Single pytest node id under tests/, e.g. tests/unit/foo.py::test_bar",
        },
    },
}


def build_run_pytest_custom_tool(*, repo_root: Path) -> dict[str, AgentCustomToolSpec]:
    """Вернуть map с одним custom tool ``run_pytest``."""
    runtime = RepairHopRunPytestRuntime(repo_root=repo_root)
    return {
        RUN_PYTEST_TOOL_NAME: AgentCustomToolSpec(
            description=(
                "Run pytest for exactly one tests/ target (node id or test module path). "
                "Use node_id only — never shell or poetry commands."
            ),
            input_schema=_RUN_PYTEST_SCHEMA,
            execute=runtime.run_pytest,
        ),
    }