"""Codex app-server host, applying OPSX tool_call_guard before every dynamic tool.

Codex runs read-only with native shell disabled. Mutations remain prohibited until
an independent OS isolation proof exists. SDK/app-server protocol: v2 JSON-RPC.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from .codex_provider import (
    CodexProviderError,
    DEFAULT_CODEX_MODEL,
    chatgpt_only_environment,
)
from .codex_tool_gateway import CodexToolDenied, CodexToolGateway, dynamic_tool_specs


@dataclass(frozen=True)
class CodexAppServerOutcome:
    text: str
    thread_id: str
    turn_id: str
    usage: Mapping[str, Any] | None
    calls: tuple[Mapping[str, str], ...]


# Codex JSON-RPC notifications can include long agent reasoning/tool payloads.
# The default asyncio subprocess stdout StreamReader limit is only 64 KiB.
# Keep a finite bound and fail closed on oversized protocol messages.
CODEX_RPC_FRAME_LIMIT_BYTES = 8 * 1024 * 1024


class CodexServerHost:
    """Handle one RPC event without exposing direct filesystem authority to Codex."""

    FORBIDDEN_NATIVE_ITEMS = frozenset(
        {"commandExecution", "fileChange", "mcpToolCall", "collabAgentToolCall"}
    )

    def __init__(
        self, gateway: CodexToolGateway,
        identity_observer: Callable[[str, str], None] | None = None,
    ) -> None:
        self.gateway = gateway
        self.identity_observer = identity_observer
        self.thread_id = ""
        self.turn_id = ""
        self.final_text = ""
        self.done = False
        self.failed = False
        self.usage: Mapping[str, Any] | None = None

    async def response_for_async(self, event: Mapping[str, Any]) -> dict[str, Any] | None:
        if event.get("method") != "item/tool/call":
            return self.response_for(event)
        params = event.get("params")
        if not isinstance(params, Mapping) or (
            params.get("tool") != "run_pytest"
            and params.get("tool") not in self.gateway.capability_specs
        ):
            return self.response_for(event)
        if ("id" not in event or not self.thread_id or not self.turn_id
                or params.get("threadId") != self.thread_id
                or params.get("turnId") != self.turn_id
                or params.get("namespace") not in (None, "opsx")):
            self.failed = True
            return self._deny(event, "CODEX_TOOL_IDENTITY_MISMATCH")
        try:
            data = await self.gateway.dispatch_async(
                str(params.get("tool")), params.get("arguments"),
            )
        except CodexToolDenied as exc:
            self.failed = True
            return self._deny(event, exc.code)
        return {"id": event["id"], "result": {
            "contentItems": [{"type": "inputText", "text": data}], "success": True,
        }}

    def response_for(self, event: Mapping[str, Any]) -> dict[str, Any] | None:
        method = event.get("method")
        params = event.get("params")
        if not isinstance(params, Mapping):
            params = {}
        if "id" in event and method == "item/tool/call":
            if not self.thread_id or not self.turn_id:
                self.failed = True
                return self._deny(event, "CODEX_TOOL_BEFORE_TURN")
            if params.get("threadId") != self.thread_id or params.get("turnId") != self.turn_id:
                self.failed = True
                return self._deny(event, "CODEX_TOOL_IDENTITY_MISMATCH")
            if params.get("namespace") not in (None, "opsx"):
                self.failed = True
                return self._deny(event, "CODEX_UNAUTHORIZED_NAMESPACE")
            tool = str(params.get("tool") or "")
            args = params.get("arguments")
            if not isinstance(args, Mapping):
                self.failed = True
                return self._deny(event, "CODEX_BAD_TOOL_ARGUMENTS")
            try:
                data = self.gateway.dispatch(tool, args)
            except CodexToolDenied as exc:
                self.failed = True
                return self._deny(event, exc.code)
            return {
                "id": event["id"],
                "result": {"contentItems": [{"type": "inputText", "text": data}], "success": True},
            }

        if "id" in event and method in {
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        }:
            self.failed = True
            return {"id": event["id"], "result": {"decision": "decline"}}
        if "id" in event and method == "item/permissions/requestApproval":
            self.failed = True
            return {"id": event["id"], "result": {"permissions": []}}
        if "id" in event and method is not None:
            self.failed = True
            return {"id": event["id"], "error": {"code": -32601, "message": "OPSX_DENIED_UNKNOWN_REQUEST"}}
        if method == "item/started":
            item = params.get("item")
            if isinstance(item, Mapping) and item.get("type") in self.FORBIDDEN_NATIVE_ITEMS:
                self.failed = True
        if method == "item/completed":
            item = params.get("item")
            if isinstance(item, Mapping):
                if item.get("type") in self.FORBIDDEN_NATIVE_ITEMS:
                    self.failed = True
                if item.get("type") == "agentMessage":
                    raw = item.get("text")
                    if isinstance(raw, str) and raw.strip():
                        self.final_text = raw
        if method == "turn/started":
            turn = params.get("turn")
            if isinstance(turn, Mapping) and isinstance(turn.get("id"), str):
                self.turn_id = turn["id"]
                if self.identity_observer is not None:
                    self.identity_observer("turn", self.turn_id)
        if method == "turn/completed":
            turn = params.get("turn")
            self.done = True
            if not isinstance(turn, Mapping) or turn.get("status") != "completed":
                self.failed = True
        if method == "error" and not params.get("willRetry"):
            self.failed = True
        return None

    @staticmethod
    def _deny(event: Mapping[str, Any], code: str) -> dict[str, Any]:
        return {
            "id": event["id"],
            "result": {
                "contentItems": [{"type": "inputText", "text": code}],
                "success": False,
            },
        }


async def _send(proc: asyncio.subprocess.Process, data: Mapping[str, Any]) -> None:
    if proc.stdin is None:
        raise CodexProviderError("CODEX_APP_SERVER_STDIN_MISSING")
    proc.stdin.write((json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8"))
    await proc.stdin.drain()


async def _line(proc: asyncio.subprocess.Process, deadline: float) -> dict[str, Any]:
    if proc.stdout is None:
        raise CodexProviderError("CODEX_APP_SERVER_STDOUT_MISSING")
    try:
        raw = await asyncio.wait_for(proc.stdout.readline(), timeout=max(0.01, deadline - time.monotonic()))
    except TimeoutError as exc:
        raise CodexProviderError("CODEX_APP_SERVER_TIMEOUT") from exc
    except ValueError as exc:
        raise CodexProviderError("CODEX_APP_SERVER_RPC_FRAME_TOO_LARGE") from exc
    if len(raw) > CODEX_RPC_FRAME_LIMIT_BYTES:
        raise CodexProviderError("CODEX_APP_SERVER_RPC_FRAME_TOO_LARGE")
    if not raw:
        raise CodexProviderError("CODEX_APP_SERVER_DISCONNECTED")
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise CodexProviderError("CODEX_APP_SERVER_BAD_JSON") from exc
    if not isinstance(value, dict):
        raise CodexProviderError("CODEX_APP_SERVER_BAD_JSON")
    return value


def _native_server_binary(binary: str) -> str:
    """Avoid npm cmd -> node -> codex.exe process chains on Windows."""
    launcher = Path(binary)
    if os.name != "nt" or launcher.suffix.lower() not in {".cmd", ".bat"}:
        return binary
    vendor = (
        launcher.parent / "node_modules" / "@openai" / "codex"
        / "node_modules" / "@openai"
    )
    matches = sorted(vendor.glob("codex-win32-*/vendor/*/bin/codex.exe"))
    if len(matches) != 1:
        raise CodexProviderError("CODEX_NATIVE_BINARY_NOT_FOUND")
    return str(matches[0])


async def run_guarded_codex_turn(
    gateway: CodexToolGateway,
    *,
    prompt: str,
    model: str = DEFAULT_CODEX_MODEL,
    reasoning_effort: str = "low",
    timeout_seconds: int = 300,
    executable: str | None = None,
    identity_observer: Callable[[str, str], None] | None = None,
    resume_thread_id: str | None = None,
    ephemeral: bool = True,
) -> CodexAppServerOutcome:
    """One bounded, isolated read-only turn. Never resumes an unknown active run."""
    if not prompt.strip():
        raise CodexProviderError("CODEX_PROMPT_EMPTY")
    if not 1 <= timeout_seconds <= 3600:
        raise CodexProviderError("CODEX_TIMEOUT_INVALID")
    if reasoning_effort not in {"low", "medium", "high"}:
        raise CodexProviderError("CODEX_REASONING_EFFORT_INVALID")
    binary = executable or shutil.which("codex")
    if not binary:
        raise CodexProviderError("CODEX_CLI_NOT_INSTALLED")
    env = chatgpt_only_environment()
    server_binary = _native_server_binary(binary)
    login = await asyncio.to_thread(
        subprocess.run,
        [binary, "login", "status"],
        env=env, capture_output=True, text=True, timeout=20, check=False,
    )
    if login.returncode != 0 or "logged in using chatgpt" not in ((login.stdout or "") + (login.stderr or "")).lower():
        raise CodexProviderError("CODEX_CHATGPT_LOGIN_REQUIRED")
    with tempfile.TemporaryDirectory(prefix="opsx-codex-host-") as isolated:
        sandbox = Path(isolated)
        # No source files or credentials are mounted under Codex cwd.
        await asyncio.to_thread(
            subprocess.run, ["git", "init", "-q"], cwd=isolated,
            capture_output=True, check=True,
        )
        command = [
            server_binary, "app-server", "--stdio",
            "--disable", "shell_tool",
            "--disable", "unified_exec",
            "-c", "web_search=\"disabled\"",
        ]
        proc = await asyncio.create_subprocess_exec(
            *command,
            limit=CODEX_RPC_FRAME_LIMIT_BYTES,
            cwd=isolated,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        host = CodexServerHost(gateway, identity_observer=identity_observer)
        deadline = time.monotonic() + timeout_seconds
        try:
            await _send(proc, {
                "method": "initialize", "id": 1,
                "params": {
                    "clientInfo": {
                        "name": "opsx_codex_guarded",
                        "title": "OPSX Codex Guarded Adapter",
                        "version": "0.1.0",
                    },
                    "capabilities": {"experimentalApi": True},
                },
            })
            for _ in range(100):
                event = await _line(proc, deadline)
                if event.get("id") == 1:
                    if "result" not in event:
                        raise CodexProviderError("CODEX_APP_SERVER_INITIALIZE_FAILED")
                    break
            else:
                raise CodexProviderError("CODEX_APP_SERVER_HANDSHAKE_LIMIT")
            await _send(proc, {"method": "initialized", "params": {}})
            developer_instructions = (
                "Use only the explicitly provided OPSX host tools for all "
                "file reads and writes. Never invoke shell, native patch, "
                "MCP/app tools, or subagents. The Codex execution workspace "
                "is intentionally empty; all project operations go through "
                "host tools guarded by OPSX. If this turn grants any "
                "dynamicTools, invoke at least one of those named tools "
                "before providing a final answer. Available names: "
                + ", ".join(sorted(gateway.allowed))
                + ". A response without an actual host tool call fails."
            )
            if resume_thread_id:
                method = "thread/resume"
                params = {
                    "threadId": resume_thread_id,
                    "model": model,
                    "modelProvider": "openai",
                    "cwd": str(sandbox),
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                    "developerInstructions": developer_instructions,
                }
            else:
                method = "thread/start"
                params = {
                    "model": model,
                    "allowProviderModelFallback": False,
                    "modelProvider": "openai",
                    "cwd": str(sandbox),
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                    "ephemeral": ephemeral,
                    "dynamicTools": dynamic_tool_specs(
                        tuple(sorted(gateway.allowed)),
                        capability_specs=gateway.capability_specs,
                    ),
                    "developerInstructions": developer_instructions,
                }
            await _send(proc, {"method": method, "id": 2, "params": params})
            for _ in range(300):
                event = await _line(proc, deadline)
                if event.get("id") == 2:
                    thread = (event.get("result") or {}).get("thread")
                    if not isinstance(thread, dict) or not isinstance(thread.get("id"), str):
                        raise CodexProviderError("CODEX_APP_SERVER_THREAD_START_FAILED")
                    host.thread_id = thread["id"]
                    if resume_thread_id:
                        reply = event.get("result") or {}
                        if (
                            host.thread_id != resume_thread_id
                            or reply.get("model") != model
                            or reply.get("modelProvider") != "openai"
                            or Path(str(reply.get("cwd") or "")).resolve() != sandbox
                        ):
                            raise CodexProviderError("CODEX_RESUME_PROVIDER_IDENTITY_MISMATCH")
                    if identity_observer is not None:
                        identity_observer("thread", host.thread_id)
                    break
            else:
                raise CodexProviderError("CODEX_APP_SERVER_THREAD_LIMIT")
            await _send(proc, {
                "method": "turn/start", "id": 3,
                "params": {
                    "threadId": host.thread_id,
                    "input": [{"type": "text", "text": prompt}],
                    "cwd": str(sandbox),
                    "approvalPolicy": "never",
                    "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
                    "model": model,
                    "effort": reasoning_effort,
                },
            })
            for _ in range(4000):
                event = await _line(proc, deadline)
                if event.get("id") == 3:
                    turn = (event.get("result") or {}).get("turn")
                    if isinstance(turn, Mapping) and isinstance(turn.get("id"), str):
                        host.turn_id = turn["id"]
                        if identity_observer is not None:
                            identity_observer("turn", host.turn_id)
                    else:
                        raise CodexProviderError("CODEX_APP_SERVER_TURN_START_FAILED")
                reply = await host.response_for_async(event)
                if reply is not None:
                    await _send(proc, reply)
                if host.failed:
                    # An unauthorized native tool may already have been invoked.
                    # Read-only OS sandbox is an additional boundary; never accept output.
                    raise CodexProviderError("CODEX_APP_SERVER_TOOL_GATE_VIOLATION")
                if host.done:
                    break
            if not host.done or not host.final_text or not host.turn_id:
                raise CodexProviderError("CODEX_APP_SERVER_INCOMPLETE")
            gateway.require_tool_evidence()
            # An edit grant does not force a write: verify-only/adoption hops may
            # have edit capability without using it. But any submitted edit
            # must have a proven net effect, never an agent self-report.
            if any(c.get("tool") == "edit" and c.get("verdict") == "ALLOWED"
                   for c in gateway.calls):
                gateway.require_net_edit()
            gateway.confirm_edits()  # evidence-bound completion
            return CodexAppServerOutcome(
                text=host.final_text, thread_id=host.thread_id,
                turn_id=host.turn_id, usage=host.usage,
                calls=tuple(host.gateway.calls),
            )
        except BaseException:
            # Rollback only identity-matched provisional edits; on ambiguity PAUSE.
            gateway.rollback_edits()
            raise
        finally:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except TimeoutError:
                    proc.kill()
                    await proc.wait()


class CodexGuardedLangGraphAdapter:
    """Compatibility port for the existing synchronous verification backend.

    It delegates every file operation to the same ABCAdapter/tool_call_guard
    contract used by OPSX. Does not use the unguarded Codex CLI execution path.
    """

    def __init__(self, repo: Path, *, model: str = DEFAULT_CODEX_MODEL):
        self.repo = Path(repo).resolve()
        self.model = model

    def bootstrap_agent(self) -> str:
        return "codex-guarded-per-hop"

    def execute_prompt(self, prompt: str, *, step: str | None = None):
        if step != "next_check":
            raise CodexProviderError("CODEX_WRITE_AUTHORITY_UNPROVEN")
        import sys
        from .codex_provider import CodexPromptInvocation
        agents_root = Path(__file__).resolve().parent.parent / "opsx" / "opsx_runtime"
        if not agents_root.is_dir():
            raise CodexProviderError("CODEX_OPSX_RUNTIME_MISSING")
        if str(agents_root) not in sys.path:
            sys.path.insert(0, str(agents_root))
        from agents.adapters.codex_app_server.codex_adapter import CodexAppServerAdapter
        adapter = CodexAppServerAdapter()
        result = asyncio.run(adapter._run_turn(
            {"repository_path": str(self.repo), "workspace": str(self.repo),
             "model": {"id": self.model}},
            prompt=prompt,
            tools=("read", "grep", "glob", "ls"),
            agent_key="impl_verify",
        ))
        return CodexPromptInvocation(
            prompt=prompt,
            raw_output=result.text,
            exit_code=0,
            session_id=result.agent_id,
            model=self.model,
            usage=result.usage,
        )
