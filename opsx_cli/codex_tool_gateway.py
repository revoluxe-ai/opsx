"""OPSX-authorized host-side tools for Codex app-server dynamicTools.

No Codex-native command or patch tool is trusted as a substitute for this gate.
"""
from __future__ import annotations

import asyncio
import json
import hashlib
import os
import re
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from .codex_write_journal import CodexWriteJournal


READ_TOOLS = frozenset({"read", "grep", "glob", "ls"})
EDIT_TOOLS = frozenset({"edit"})
SUPPORTED_TOOLS = READ_TOOLS | EDIT_TOOLS | frozenset({"run_pytest"})
CAPABILITY_TOOLS = frozenset({
    "search_repository", "read_source", "submit_apply_design",
})
MAX_READ_BYTES = 1_000_000
MAX_RESULTS = 100


class CodexToolDenied(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def dynamic_tool_specs(
    tools: Sequence[str], *,
    capability_specs: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Expose explicitly authorized builtins or provider-neutral capabilities."""
    capabilities = dict(capability_specs or {})
    if set(capabilities) - CAPABILITY_TOOLS:
        raise CodexToolDenied("CODEX_CAPABILITY_UNREGISTERED")
    if len(set(tools)) != len(tools) or set(tools) - (SUPPORTED_TOOLS | set(capabilities)):
        raise CodexToolDenied("CODEX_UNSUPPORTED_TOOL_AUTHORITY")
    spec = {
        "edit": ("Replace exactly one authorized text span; to create a new file, set old_string to empty and require absent path", {"path": {"type": "string"}, "old_string": {"type": "string"}, "new_string": {"type": "string"}}, ["path", "old_string", "new_string"]),
        "run_pytest": ("Run a bounded pytest node through the registered OPSX host tool",
                       {"node_id": {"type": "string"}}, ["node_id"]),
        "read": (
            "Read the contents of one existing authorized regular file, not a directory. "
            "This tool cannot list directories; a directory path returns "
            "CODEX_READ_REQUIRES_FILE. Missing files return CODEX_FILE_NOT_READABLE. "
            "A read failure alone is not evidence that OPSX denied the path.",
            {"path": {"type": "string"}}, ["path"],
        ),
        "ls": ("List an explicitly authorized workspace directory", {"path": {"type": "string"}}, ["path"]),
        "glob": ("Find files using an authorized relative glob", {"pattern": {"type": "string"}}, ["pattern"]),
        "grep": (
            "Search one authorized text file", {"path": {"type": "string"}, "pattern": {"type": "string"}},
            ["path", "pattern"],
        ),
    }
    return [
        {
            "type": "function",
            "name": tool,
            "description": capabilities[tool].description if tool in capabilities else spec[tool][0],
            "inputSchema": dict(capabilities[tool].input_schema) if tool in capabilities else {
                "type": "object", "properties": spec[tool][1],
                "required": spec[tool][2], "additionalProperties": False,
            },
        }
        for tool in tools
    ]


class CodexToolGateway:
    """All filesystem calls reach OPSX guard and remain within a canonical repo root."""

    def __init__(
        self,
        root: Path,
        tools: Sequence[str],
        *,
        guard: Callable[..., None] | None,
        read_scope: Any | None,
        journal: CodexWriteJournal | None = None,
        registered_tools: Mapping[str, Any] | None = None,
        capability_specs: Mapping[str, Any] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.allowed = frozenset(tools)
        self.capability_specs = dict(capability_specs or {})
        dynamic_tool_specs(tools, capability_specs=self.capability_specs)
        if guard is None or read_scope is None:
            raise CodexToolDenied("CODEX_OPSX_GUARD_REQUIRED")
        self.guard = guard
        self.read_scope = read_scope
        self.calls: list[dict[str, str]] = []
        self._edited_before: dict[Path, bytes] = {}
        self.journal = journal
        self.registered_tools = dict(registered_tools or {})
        if set(self.registered_tools) - {"run_pytest"}:
            raise CodexToolDenied("CODEX_CUSTOM_TOOL_UNREGISTERED")
        if "run_pytest" in self.allowed and "run_pytest" not in self.registered_tools:
            raise CodexToolDenied("CODEX_PYTEST_TOOL_UNBOUND")
        if "edit" in self.allowed:
            from agents.config import effective_restrict_allow_for_tool
            if not effective_restrict_allow_for_tool(read_scope, "edit"):
                raise CodexToolDenied("CODEX_WRITE_SCOPE_REQUIRED")
            if journal is None:
                raise CodexToolDenied("CODEX_WRITE_JOURNAL_REQUIRED")
            if journal.root != self.root:
                raise CodexToolDenied("CODEX_WRITE_JOURNAL_ROOT_MISMATCH")

    def _path(self, value: Any) -> Path:
        if not isinstance(value, str) or not value or "\x00" in value:
            raise CodexToolDenied("CODEX_INVALID_PATH")
        rel = Path(value.replace("\\", "/"))
        if rel.is_absolute() or ".." in rel.parts or ":" in value:
            raise CodexToolDenied("CODEX_PATH_ESCAPE")
        target = (self.root / rel).resolve()
        if not target.is_relative_to(self.root):
            raise CodexToolDenied("CODEX_PATH_ESCAPE")
        return target

    def _authorize(
        self, tool: str, args: Mapping[str, Any],
        record: Mapping[str, Any] | None = None,
    ) -> None:
        try:
            if record is None:
                self.guard(tool, args)
            else:
                self.guard(tool, args, record)
        except Exception as exc:
            self.calls.append({"tool": tool, "verdict": "DENIED"})
            raise CodexToolDenied("CODEX_OPSX_TOOL_GUARD_DENIED") from exc

    def _read_one(self, target: Path) -> str:
        if target.is_dir():
            # A directory read is a tool-shape mismatch, not an authority
            # violation. Never turn the file-only read into implicit ls.
            raise CodexToolDenied("CODEX_READ_REQUIRES_FILE")
        if not target.is_file() or target.stat().st_size > MAX_READ_BYTES:
            raise CodexToolDenied("CODEX_FILE_NOT_READABLE")
        return target.read_text(encoding="utf-8", errors="replace")

    def rollback_edits(self) -> None:
        """Revision-aware rollback; refuse to overwrite external edits."""
        if self.journal is not None:
            self.journal.recover(self._atomic_replace)
        self._edited_before.clear()

    def require_tool_evidence(self) -> None:
        """A model's completion text cannot stand in for a required host call."""
        if self.allowed and not any(
            c.get("verdict") == "ALLOWED" and c.get("tool") in self.allowed
            for c in self.calls
        ):
            raise CodexToolDenied("CODEX_NO_TOOL_EVIDENCE")

    def require_net_edit(self) -> None:
        """A writable Codex turn cannot claim completion without an effective host edit."""
        if not any(c.get("tool") == "edit" and c.get("verdict") == "ALLOWED"
                   for c in self.calls):
            raise CodexToolDenied("CODEX_NO_EDIT_EVIDENCE")
        if not self._edited_before or not any(
            target.is_file() and target.read_bytes() != before
            for target, before in self._edited_before.items()
        ):
            raise CodexToolDenied("CODEX_NO_NET_EDIT")

    def confirm_edits(self) -> None:
        if self.journal is not None:
            self.journal.confirm()
        self._edited_before.clear()

    def _check_static_write_scope(self, rel_path: str) -> None:
        """Enforce explicit allow/deny independently of OPSX warn/off mode."""
        from agents.config import (
            effective_restrict_allow_for_tool,
            path_allowed_by_scope,
            path_matches_pattern,
        )
        # The common OPSX policy is authoritative; preserve the distinct
        # deny-path receipt code without inventing another permission rule.
        if any(path_matches_pattern(rel_path, p) for p in
               (getattr(self.read_scope, "deny", ()) or ())):
            raise CodexToolDenied("CODEX_WRITE_DENIED_PATH")
        patterns = effective_restrict_allow_for_tool(self.read_scope, "edit")
        if not patterns or not path_allowed_by_scope(
            self.read_scope, rel_path, restrict_allow=patterns,
        ):
            raise CodexToolDenied("CODEX_WRITE_OUTSIDE_ALLOWLIST")

    @staticmethod
    def _atomic_replace(target: Path, content: bytes) -> None:
        name = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", dir=target.parent, delete=False) as stream:
                name = stream.name
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, target)
        finally:
            if name is not None and Path(name).exists():
                Path(name).unlink()

    @staticmethod
    def _atomic_create(target: Path, content: bytes) -> None:
        """Atomic no-clobber file publish (hard-link installed, not os.replace)."""
        name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=target.parent, delete=False,
            ) as stream:
                name = stream.name
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(name, target)
            except FileExistsError as exc:
                raise CodexToolDenied("CODEX_CREATE_CONCURRENT_MODIFICATION") from exc
            except OSError as exc:
                raise CodexToolDenied("CODEX_ATOMIC_CREATE_UNAVAILABLE") from exc
        finally:
            if name is not None and Path(name).exists():
                Path(name).unlink()

    def _edit_one(self, args: Mapping[str, Any]) -> str:
        if set(args) != {"path", "old_string", "new_string"}:
            raise CodexToolDenied("CODEX_BAD_TOOL_ARGUMENTS")
        old, new = args["old_string"], args["new_string"]
        if not isinstance(old, str) or not isinstance(new, str) or not new:
            raise CodexToolDenied("CODEX_BAD_EDIT")
        target = self._path(args["path"])
        rel = target.relative_to(self.root).as_posix()
        clean_args = {"path": rel, "old_string": old, "new_string": new}
        self._check_static_write_scope(rel)
        self._authorize("edit", clean_args, {"kind": "tool_call", "status": "running"})
        if not old:
            # Explicit create precondition. No parent directory creation or
            # overwrites; the caller must already have write authority.
            if len(new.encode("utf-8")) > MAX_READ_BYTES:
                raise CodexToolDenied("CODEX_EDIT_TOO_LARGE")
            if target.exists() or not target.parent.is_dir():
                raise CodexToolDenied("CODEX_CREATE_PRECONDITION_FAILED")
            assert self.journal is not None
            after = new.encode("utf-8")
            self.journal.begin_create(rel, after)
            self._edited_before.setdefault(target, b"")
            try:
                self._atomic_create(target, after)
                self._authorize("edit", clean_args, {"kind": "tool_call", "status": "completed"})
            except BaseException:
                self.rollback_edits()
                raise
            self.calls.append({"tool": "edit", "verdict": "ALLOWED"})
            return "FILE_CREATED sha256=" + hashlib.sha256(after).hexdigest()
        if not target.is_file() or target.stat().st_size > MAX_READ_BYTES:
            raise CodexToolDenied("CODEX_EDIT_TARGET_INVALID")
        before = target.read_bytes()
        try:
            text = before.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CodexToolDenied("CODEX_EDIT_NOT_UTF8") from exc
        if text.count(old) != 1:
            raise CodexToolDenied("CODEX_EDIT_EXPECTED_ONCE")
        after = text.replace(old, new, 1).encode("utf-8")
        if len(after) > MAX_READ_BYTES:
            raise CodexToolDenied("CODEX_EDIT_TOO_LARGE")
        if target.read_bytes() != before:
            raise CodexToolDenied("CODEX_EDIT_CONCURRENT_MODIFICATION")
        assert self.journal is not None
        self.journal.begin(rel, before, after)  # fsync evidence before any mutation
        self._edited_before.setdefault(target, before)
        try:
            self._atomic_replace(target, after)
            self._authorize("edit", clean_args, {"kind": "tool_call", "status": "completed"})
        except BaseException:
            self.rollback_edits()
            raise
        self.calls.append({"tool": "edit", "verdict": "ALLOWED"})
        return "EDIT_APPLIED sha256=" + hashlib.sha256(after).hexdigest()

    async def dispatch_async(self, tool: str, args: Mapping[str, Any]) -> str:
        if tool in self.capability_specs:
            if tool not in self.allowed or not isinstance(args, Mapping):
                raise CodexToolDenied("CODEX_CAPABILITY_NOT_AUTHORIZED")
            schemas = {
                "search_repository": ({"query", "domains"}, set()),
                "read_source": ({"source_id"}, {"cursor", "expected_revision"}),
                "submit_apply_design": ({"content", "base_digest"}, set()),
            }
            required, optional = schemas[tool]
            if not required.issubset(args) or set(args) - required - optional:
                raise CodexToolDenied("CODEX_CAPABILITY_ARGUMENTS_INVALID")
            if tool == "search_repository" and (
                not isinstance(args["query"], str)
                or not isinstance(args["domains"], list)
                or not args["domains"]
            ):
                raise CodexToolDenied("CODEX_CAPABILITY_ARGUMENTS_INVALID")
            if tool == "read_source" and (
                not isinstance(args["source_id"], str)
                or ("cursor" in args and (type(args["cursor"]) is not int or args["cursor"] < 0))
            ):
                raise CodexToolDenied("CODEX_CAPABILITY_ARGUMENTS_INVALID")
            if tool == "submit_apply_design" and (
                not isinstance(args["base_digest"], str)
                or not isinstance(args["content"], (str, dict))
            ):
                raise CodexToolDenied("CODEX_CAPABILITY_ARGUMENTS_INVALID")
            try:
                # Capability was pre-bound to the exact manifest and current
                # change by the Codex adapter. No freeform path or shell tool.
                result = await asyncio.wait_for(
                    self.capability_specs[tool].execute(dict(args), None),
                    timeout=150,
                )
            except BaseException as exc:
                self.calls.append({"tool": tool, "verdict": "FAILED"})
                raise CodexToolDenied("CODEX_CAPABILITY_EXECUTION_FAILED") from exc
            if not isinstance(result, Mapping):
                self.calls.append({"tool": tool, "verdict": "FAILED"})
                raise CodexToolDenied("CODEX_CAPABILITY_RESULT_INVALID")
            self.calls.append({"tool": tool, "verdict": (
                "ALLOWED" if result.get("ok") is not False else "FAILED"
            )})
            return json.dumps(dict(result), ensure_ascii=False)
        if tool != "run_pytest":
            return self.dispatch(tool, args)
        if tool not in self.allowed or "run_pytest" not in self.registered_tools:
            raise CodexToolDenied("CODEX_PYTEST_TOOL_UNBOUND")
        if not isinstance(args, Mapping) or set(args) != {"node_id"}:
            raise CodexToolDenied("CODEX_BAD_TOOL_ARGUMENTS")
        node_id = args["node_id"]
        if not isinstance(node_id, str) or not node_id or len(node_id) > 500:
            raise CodexToolDenied("CODEX_PYTEST_NODE_INVALID")
        self._authorize("run_pytest", {"node_id": node_id})
        spec = self.registered_tools["run_pytest"]
        try:
            # This is a deterministic pre-registered tool; it never receives a shell command.
            result = await asyncio.wait_for(spec.execute({"node_id": node_id}, None), timeout=150)
        except Exception as exc:
            self.calls.append({"tool": tool, "verdict": "FAILED"})
            raise CodexToolDenied("CODEX_PYTEST_TOOL_FAILED") from exc
        if not isinstance(result, Mapping):
            raise CodexToolDenied("CODEX_PYTEST_RESULT_INVALID")
        self.calls.append({"tool": tool, "verdict": "ALLOWED"})
        return json.dumps(dict(result), ensure_ascii=False)

    def dispatch(self, tool: str, args: Mapping[str, Any]) -> str:
        if tool not in self.allowed:
            raise CodexToolDenied("CODEX_TOOL_NOT_AUTHORIZED")
        if not isinstance(args, Mapping):
            raise CodexToolDenied("CODEX_BAD_TOOL_ARGUMENTS")
        if tool == "edit":
            return self._edit_one(args)
        if tool == "read":
            if set(args) != {"path"}:
                raise CodexToolDenied("CODEX_BAD_TOOL_ARGUMENTS")
            target = self._path(args["path"])
            self._authorize(tool, {"path": target.relative_to(self.root).as_posix()})
            result = self._read_one(target)
        elif tool == "ls":
            if set(args) != {"path"}:
                raise CodexToolDenied("CODEX_BAD_TOOL_ARGUMENTS")
            target = self._path(args["path"])
            self._authorize(tool, {"path": target.relative_to(self.root).as_posix()})
            if not target.is_dir():
                raise CodexToolDenied("CODEX_DIRECTORY_MISSING")
            items = []
            for item in sorted(target.iterdir())[:MAX_RESULTS]:
                rel = item.relative_to(self.root).as_posix()
                try:
                    self._authorize("read", {"path": rel})
                except CodexToolDenied:
                    continue
                items.append(rel + ("/" if item.is_dir() else ""))
            result = "\n".join(items)
        elif tool == "glob":
            if set(args) != {"pattern"}:
                raise CodexToolDenied("CODEX_BAD_TOOL_ARGUMENTS")
            pattern = args["pattern"]
            if not isinstance(pattern, str) or not pattern or ".." in Path(pattern).parts or ":" in pattern or pattern.startswith("/"):
                raise CodexToolDenied("CODEX_BAD_GLOB")
            self._authorize("glob", {"pattern": pattern})
            matches = []
            for item in self.root.glob(pattern):
                if len(matches) >= MAX_RESULTS:
                    break
                try:
                    resolved = item.resolve()
                    if not resolved.is_relative_to(self.root):
                        continue
                    rel = resolved.relative_to(self.root).as_posix()
                    self._authorize("read", {"path": rel})
                except CodexToolDenied:
                    continue
                matches.append(rel)
            result = "\n".join(matches)
        elif tool == "grep":
            if set(args) != {"path", "pattern"}:
                raise CodexToolDenied("CODEX_BAD_TOOL_ARGUMENTS")
            target = self._path(args["path"])
            pattern = args["pattern"]
            if not isinstance(pattern, str) or len(pattern) > 512:
                raise CodexToolDenied("CODEX_BAD_GREP")
            self._authorize(tool, {"path": target.relative_to(self.root).as_posix(), "pattern": pattern})
            try:
                rx = re.compile(pattern)
            except re.error as exc:
                raise CodexToolDenied("CODEX_BAD_GREP") from exc
            lines = self._read_one(target).splitlines()
            result = "\n".join(f"{i}: {line[:500]}" for i, line in enumerate(lines, 1) if rx.search(line))[:MAX_READ_BYTES]
        else:
            raise CodexToolDenied("CODEX_TOOL_NOT_AUTHORIZED")
        self.calls.append({"tool": tool, "verdict": "ALLOWED"})
        return result
