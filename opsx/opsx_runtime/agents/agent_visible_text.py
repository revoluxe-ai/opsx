"""Санитизация текста и JSON для агентов и user-facing run log (без slash-команд)."""

from __future__ import annotations

import copy
import re
from typing import Any

_SLASH_OPSX_RE = re.compile(r"/opsx-[A-Za-z0-9_-]+(?:\s+[^\s`\"'\\)]+)?")
_COMMAND_PATH_RE = re.compile(r"\.cursor/commands/[^\s`'\"\\)]+", re.IGNORECASE)
_RU_COMMAND_PHRASE_RE = re.compile(
    r"\(команда\s*`?/opsx-[^)`]+`?\)",
    re.IGNORECASE,
)
_LEGACY_HANDOFF_KEYS = frozenset(
    {
        "primitive_command",
        "implementation_primitive_command",
        "next_command",
        "local_hop_copy_block",
        "next_action",
        "command",
        "permitted_primitive_commands",
        "bound_primitive_command",
    },
)


def strip_slash_commands_from_text(text: str) -> str:
    """Убрать ``/opsx-*``, пути command-файлов и фразу ``(команда `/opsx-...`)``."""
    out = str(text or "")
    out = _RU_COMMAND_PHRASE_RE.sub("(стадия runtime)", out)
    out = _COMMAND_PATH_RE.sub("[opsx-contract]", out)
    out = _SLASH_OPSX_RE.sub("[opsx-stage]", out)
    return out


def sanitize_value_for_agent_view(value: Any) -> Any:
    """Рекурсивно очистить mapping/list/str для prompt и видимого run log."""
    if isinstance(value, str):
        return strip_slash_commands_from_text(value)
    if isinstance(value, list):
        return [sanitize_value_for_agent_view(item) for item in value]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if str(key) in _LEGACY_HANDOFF_KEYS:
                continue
            if str(key) == "implementation_plan" and isinstance(item, dict):
                trimmed = {
                    k: v
                    for k, v in item.items()
                    if str(k) not in _LEGACY_HANDOFF_KEYS
                }
                out[key] = sanitize_value_for_agent_view(trimmed)
                continue
            out[str(key)] = sanitize_value_for_agent_view(item)
        return out
    return value


def sanitize_mapping_for_agent_prompt(data: dict[str, Any] | None) -> dict[str, Any]:
    """Глубокая копия mapping без handoff/slash полей для JSON в SDK prompt."""
    if not isinstance(data, dict):
        return {}
    return sanitize_value_for_agent_view(copy.deepcopy(data))


__all__ = [
    "sanitize_mapping_for_agent_prompt",
    "sanitize_value_for_agent_view",
    "strip_slash_commands_from_text",
]