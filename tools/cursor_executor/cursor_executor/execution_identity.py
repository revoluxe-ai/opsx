"""Stable execution_id helpers for Temporal activity retries."""

from __future__ import annotations

import hashlib
import re


def _slug(value: str, *, max_len: int = 80) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip())
    return cleaned[:max_len] or "exec"


def prompt_digest(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


def execution_id_for_prompt(
    *,
    workflow_key: str,
    session_epoch: str,
    step: str | None,
    prompt: str,
) -> str:
    step_part = _slug(step or "prompt", max_len=32)
    return _slug(
        f"{workflow_key}:{session_epoch}:{step_part}:{prompt_digest(prompt)}",
        max_len=180,
    )


def bootstrap_execution_id(*, workflow_key: str, session_epoch: str) -> str:
    return _slug(f"{workflow_key}:{session_epoch}:agent-bootstrap", max_len=180)
