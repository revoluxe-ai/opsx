"""Custom ``read`` для impl_verify: scope-check до чтения с диска (без built-in read)."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from collections.abc import Mapping, Sequence
from typing import Any

from agents.adapters.agent_capability_options import AgentCustomToolSpec
from agents.config import ReadScope, check_paths_in_scope, to_repo_relative
from agents.verify.scenario_coverage_verify.mapped_test_view import format_numbered_page

_IMPL_VERIFY_READ_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["path"],
    "properties": {
        "path": {"type": "string", "minLength": 1},
        "offset": {"type": "integer", "minimum": 1},
        "limit": {"type": "integer", "minimum": 1},
        "expected_revision": {"type": "string", "minLength": 8},
    },
}

# Только имена из model adapter CreateAgent (``ls``, не ``list_dir`` / ``listdir``).
_BUILTIN_READ_DISALLOWED = (
    "read",
    "grep",
    "glob",
    "ls",
    "shell",
)

# Встроенный read/grep/glob/ls off; ``shell``/``edit`` остаются у mutating hop.
_BUILTIN_SEARCH_DISALLOWED = (
    "read",
    "grep",
    "glob",
    "ls",
)

_APPLY_SCOPED_READ_TOOLS = frozenset({"read", "edit"})
_REPAIR_HOP_SCOPED_TOOLS = frozenset({"read", "edit", "run_pytest"})

# Полный inline-body без offset/limit: Cursor MCP/protobuf отдаёт пустой text выше ~64KiB.
_FULL_READ_INLINE_MAX_BYTES = 64_000
_DEFAULT_FULL_READ_PAGE_LINES = 400


def scoped_read_result_text(result: Mapping[str, Any]) -> str:
    """Извлечь текстовое тело успешного read из MCP или legacy-ответа инструмента."""
    content = result.get("content")
    if isinstance(content, list) and content:
        block = content[0]
        if isinstance(block, Mapping):
            return str(block.get("text") or "")
    if isinstance(content, str):
        return content
    return ""


def _mcp_tool_payload(*, text: str, is_error: bool = False, **fields: Any) -> dict[str, Any]:
    """Собрать ``CallCustomTool`` result: тело файла только в ``content[0].text``."""
    payload: dict[str, Any] = {
        "content": [{"type": "text", "text": text}],
        **fields,
    }
    if is_error:
        payload["isError"] = True
    return payload


def _mcp_tool_error(**fields: Any) -> dict[str, Any]:
    """Ошибка read: JSON в MCP-text плюс структурные поля ``ok``/``code``."""
    return _mcp_tool_payload(
        text=json.dumps(fields, ensure_ascii=False),
        is_error=True,
        **fields,
    )


class ImplVerifyScopedReadRuntime:
    """Проверка hop read-scope перед чтением файла (MCP custom tool)."""

    def __init__(self, *, scope: ReadScope, repo_root: Path) -> None:
        self._scope = scope
        self._repo_root = repo_root.resolve()

    async def read(self, args: Mapping[str, Any], _ctx: Any) -> dict[str, Any]:
        """Прочитать path или вернуть ``READ_SCOPE_VIOLATION`` без built-in SDK read."""
        if not isinstance(args, Mapping):
            return _mcp_tool_error(ok=False, code="INVALID_ARGS")
        path = str(args.get("path") or "").strip()
        if not path:
            return _mcp_tool_error(ok=False, code="INVALID_ARGS", field="path")
        checked = check_paths_in_scope(
            self._scope,
            tool_name="read",
            paths=[path],
            repo_root=self._repo_root,
        )
        if checked.violations:
            v = checked.violations[0]
            return _mcp_tool_error(
                ok=False,
                code="READ_SCOPE_VIOLATION",
                rule=v.rule,
                path=v.path,
                message=(
                    "Path is outside hop read-scope ALLOW; use embedded INPUT CONTEXT "
                    "and change-root paths only."
                ),
            )
        rel = to_repo_relative(path, self._repo_root)
        if not rel:
            return _mcp_tool_error(
                ok=False,
                code="READ_SCOPE_VIOLATION",
                rule="outside_restrict_allow",
                path=path,
            )
        target = (self._repo_root / rel).resolve()
        if not target.is_file():
            return _mcp_tool_error(ok=False, code="MISSING_FILE", path=rel)
        try:
            data = await asyncio.to_thread(target.read_text, encoding="utf-8", errors="replace")
        except OSError:
            return _mcp_tool_error(ok=False, code="READ_FAILURE", path=rel)
        revision = hashlib.sha256(data.encode("utf-8")).hexdigest()
        expected_revision = str(args.get("expected_revision") or "").strip()
        if expected_revision and expected_revision != revision:
            return _mcp_tool_error(
                ok=False,
                code="SOURCE_REVISION_CONFLICT",
                path=rel,
                source_revision=revision,
                expected_revision=expected_revision,
            )
        offset_raw = args.get("offset")
        limit_raw = args.get("limit")
        total_lines = len(data.splitlines()) if data else 0
        payload_fields: dict[str, Any] = {
            "ok": True,
            "path": rel.replace("\\", "/"),
            "totalLines": total_lines,
            "fileSize": target.stat().st_size,
            "emptyFile": (len(data) == 0),
            "source_revision": revision,
        }
        if offset_raw is not None or limit_raw is not None:
            try:
                offset = int(offset_raw if offset_raw is not None else 1)
                limit = int(limit_raw) if limit_raw is not None else None
            except (TypeError, ValueError):
                return _mcp_tool_error(ok=False, code="INVALID_ARGS", field="offset/limit")
            if offset < 1:
                return _mcp_tool_error(ok=False, code="INVALID_ARGS", field="offset")
            if limit is not None and limit < 1:
                return _mcp_tool_error(ok=False, code="INVALID_ARGS", field="limit")
            numbered, _total, start, end = format_numbered_page(
                data, offset=offset, limit=limit,
            )
            return _mcp_tool_payload(
                text=numbered,
                **payload_fields,
                offset=start,
                endLine=end,
                numbered=True,
            )
        file_size = int(payload_fields["fileSize"])
        if file_size > _FULL_READ_INLINE_MAX_BYTES:
            numbered, _total, start, end = format_numbered_page(
                data,
                offset=1,
                limit=_DEFAULT_FULL_READ_PAGE_LINES,
            )
            hint = (
                f"\n… [scoped read: file too large for full inline body "
                f"({file_size} bytes, {total_lines} lines); "
                f"use offset/limit — showing lines {start}-{end}] …\n"
            )
            return _mcp_tool_payload(
                text=hint + numbered,
                **payload_fields,
                offset=start,
                endLine=end,
                numbered=True,
                paginationRequired=True,
            )
        return _mcp_tool_payload(text=data, **payload_fields)


def build_impl_verify_scoped_read_tools(
    *,
    scope: ReadScope,
    repo_root: Path,
    include_run_pytest: bool = False,
    include_visual_cover_asset: bool = False,
    extra_custom_tool_specs: Mapping[str, AgentCustomToolSpec] | None = None,
) -> dict[str, AgentCustomToolSpec]:
    """Custom tool map: ``read`` (+ опционально ``run_pytest`` / ``visual_cover_asset``)."""
    runtime = ImplVerifyScopedReadRuntime(scope=scope, repo_root=repo_root)
    tools: dict[str, AgentCustomToolSpec] = {
        "read": AgentCustomToolSpec(
            description=(
                "Read one repository file. Only paths matching impl_verify "
                "read-scope ALLOW; repo-root files such as alembic.ini are rejected."
            ),
            input_schema=_IMPL_VERIFY_READ_SCHEMA,
            execute=runtime.read,
        ),
    }
    if include_run_pytest:
        from agents.verify.repair.run_pytest_tool import build_run_pytest_custom_tool

        tools.update(build_run_pytest_custom_tool(repo_root=repo_root))
    if include_visual_cover_asset:
        from agents.implementation.apply.visual_cover_asset_tool import (
            build_visual_cover_asset_custom_tool,
        )

        tools.update(build_visual_cover_asset_custom_tool(repo_root=repo_root))
    if extra_custom_tool_specs:
        tools.update(dict(extra_custom_tool_specs))
    return tools


def build_impl_verify_scoped_read_sdk_options(
    *,
    api_key: str,
    model: Mapping[str, Any],
    local: Mapping[str, Any],
    scope: ReadScope,
    repo_root: Path,
    extra_builtin_tools: Sequence[str] = (),
    include_run_pytest: bool = False,
    include_visual_cover_asset: bool = False,
    extra_custom_tool_specs: Mapping[str, AgentCustomToolSpec] | None = None,
) -> dict[str, Any]:
    """model adapter options: MCP custom ``read``; built-in search/read tools off."""
    from agents.adapters.agent_capability_options import custom_tool_specs_to_provider

    local_payload = dict(local)
    local_payload["setting_sources"] = []
    local_payload["custom_tools"] = custom_tool_specs_to_provider(
        build_impl_verify_scoped_read_tools(
            scope=scope,
            repo_root=repo_root,
            include_run_pytest=include_run_pytest,
            include_visual_cover_asset=include_visual_cover_asset,
            extra_custom_tool_specs=extra_custom_tool_specs,
        ),
    )
    extra = tuple(str(t).strip().lower() for t in extra_builtin_tools if str(t).strip())
    disallowed = (
        list(_BUILTIN_READ_DISALLOWED)
        if not extra
        else list(_BUILTIN_SEARCH_DISALLOWED)
    )
    if extra and "shell" not in extra:
        disallowed.append("shell")
    return {
        "apiKey": api_key,
        "model": dict(model),
        "local": local_payload,
        "tools": ["mcp", *extra],
        "disallowed_tools": disallowed,
        "mcp_servers": {},
    }


def hop_uses_scoped_read_tool(
    agent_key: str | None,
    tools: tuple[str, ...] | list[str],
) -> bool:
    """True, когда built-in read/grep/glob/ls не подключаются — только custom read."""
    key = str(agent_key or "").strip()
    normalized = {str(t).strip().lower() for t in tools if str(t).strip()}
    forbidden = frozenset({"grep", "glob", "ls", "list_dir", "listdir", "search"})
    if forbidden.intersection(normalized):
        return False
    if key in ("impl_verify", "scenario_coverage_verify", "test_triage", "CLASSIFIER"):
        return normalized == {"read"}
    if key in (
        "test_repair",
        "IMPLEMENTATION_EXECUTOR",
        "RED_TEST_WORKER",
        "GREEN_VERIFIER",
        "apply_red",
        "apply_green",
    ):
        return normalized <= _REPAIR_HOP_SCOPED_TOOLS and "read" in normalized
    if key in (
        "apply",
        "apply_design_review",
        "apply_implement",
        "scenario_test_repair",
        "scenario_test_author",
        "test_harness_repair",
    ):
        return normalized <= _APPLY_SCOPED_READ_TOOLS and "read" in normalized
    return False


def impl_verify_uses_scoped_read_tool(
    agent_key: str | None,
    tools: tuple[str, ...] | list[str],
) -> bool:
    """Backward-compatible alias для impl_verify-only check."""
    return hop_uses_scoped_read_tool(agent_key, tools)
