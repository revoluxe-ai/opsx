"""OPSX-authorized host-side tools for Codex app-server dynamicTools.

No Codex-native command or patch tool is trusted as a substitute for this gate.
"""
from __future__ import annotations

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
SUPPORTED_TOOLS = READ_TOOLS | EDIT_TOOLS
MAX_READ_BYTES = 1_000_000
MAX_RESULTS = 100


class CodexToolDenied(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def dynamic_tool_specs(tools: Sequence[str]) -> list[dict[str, Any]]:
    """Expose only tools that the caller explicitly authorized."""
    if len(set(tools)) != len(tools) or set(tools) - SUPPORTED_TOOLS:
        raise CodexToolDenied("CODEX_UNSUPPORTED_TOOL_AUTHORITY")
    spec = {
        "edit": ("Replace exactly one authorized text span with revision precondition", {"path": {"type": "string"}, "old_string": {"type": "string"}, "new_string": {"type": "string"}}, ["path", "old_string", "new_string"]),
        "read": ("Read one explicitly authorized workspace file", {"path": {"type": "string"}}, ["path"]),
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
            "description": spec[tool][0],
            "inputSchema": {
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
    ) -> None:
        self.root = root.resolve()
        self.allowed = frozenset(tools)
        dynamic_tool_specs(tools)
        if guard is None or read_scope is None:
            raise CodexToolDenied("CODEX_OPSX_GUARD_REQUIRED")
        self.guard = guard
        self.read_scope = read_scope
        self.calls: list[dict[str, str]] = []
        self._edited_before: dict[Path, bytes] = {}
        self.journal = journal
        if "edit" in self.allowed:
            if not getattr(read_scope, "restrict_write_allow", None):
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
        if not target.is_file() or target.stat().st_size > MAX_READ_BYTES:
            raise CodexToolDenied("CODEX_FILE_NOT_READABLE")
        return target.read_text(encoding="utf-8", errors="replace")

    def rollback_edits(self) -> None:
        """Revision-aware rollback; refuse to overwrite external edits."""
        if self.journal is not None:
            self.journal.recover(self._atomic_replace)
        self._edited_before.clear()

    def confirm_edits(self) -> None:
        if self.journal is not None:
            self.journal.confirm()
        self._edited_before.clear()

    def _check_static_write_scope(self, rel_path: str) -> None:
        """Enforce explicit allow/deny independently of OPSX warn/off mode."""
        try:
            from agents.config import path_matches_pattern
        except ImportError as exc:
            raise CodexToolDenied("CODEX_SCOPE_MATCHER_UNAVAILABLE") from exc
        patterns = tuple(getattr(self.read_scope, "restrict_write_allow", None) or ())
        deny = tuple(getattr(self.read_scope, "deny", ()) or ())
        if not patterns or not any(path_matches_pattern(rel_path, pat) for pat in patterns):
            raise CodexToolDenied("CODEX_WRITE_OUTSIDE_ALLOWLIST")
        if any(path_matches_pattern(rel_path, pat) for pat in deny):
            raise CodexToolDenied("CODEX_WRITE_DENIED_PATH")

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

    def _edit_one(self, args: Mapping[str, Any]) -> str:
        if set(args) != {"path", "old_string", "new_string"}:
            raise CodexToolDenied("CODEX_BAD_TOOL_ARGUMENTS")
        old, new = args["old_string"], args["new_string"]
        if not isinstance(old, str) or not old or not isinstance(new, str):
            raise CodexToolDenied("CODEX_BAD_EDIT")
        target = self._path(args["path"])
        rel = target.relative_to(self.root).as_posix()
        clean_args = {"path": rel, "old_string": old, "new_string": new}
        self._check_static_write_scope(rel)
        self._authorize("edit", clean_args, {"kind": "tool_call", "status": "running"})
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
