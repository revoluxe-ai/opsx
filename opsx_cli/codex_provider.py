"""Shared authentication and model contract for guarded Codex app-server.

No direct Codex CLI agent executor is exposed: filesystem access must
pass through CodexToolGateway and the OPSX tool_call_guard.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping

DEFAULT_CODEX_MODEL = "gpt-6-luna"
DEFAULT_REASONING_EFFORT = "low"
_ENV_API_KEYS = frozenset({
    "OPENAI_API_KEY", "CODEX_API_KEY", "AZURE_OPENAI_API_KEY",
    "OPENAI_BASE_URL", "AZURE_OPENAI_ENDPOINT",
})


class CodexProviderError(RuntimeError):
    """Fail-closed machine-readable provider error."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(code + (f": {detail}" if detail else ""))


@dataclass(frozen=True)
class CodexPromptInvocation:
    prompt: str
    raw_output: str
    exit_code: int
    session_id: str
    model: str
    usage: Mapping[str, Any] | None = None


def chatgpt_only_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Strip API keys and paid endpoint overrides; never fall back to API billing."""
    return {
        k: v for k, v in dict(os.environ if source is None else source).items()
        if k.upper() not in _ENV_API_KEYS
    }
