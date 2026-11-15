"""Единая нормализация и извлечение JSON из текстовых ответов SDK-агентов."""

from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel

TModel = TypeVar("TModel", bound=BaseModel)

_AGENT_JSON_NOT_FOUND = "Agent JSON object not found"


def _strip_outer(text: str) -> str:
    return text.strip()


def _unfence_from_start(candidate: str) -> str | None:
    """Вернуть содержимое fence, если строка начинается с markdown fence."""
    if candidate.startswith("```json\n"):
        inner = candidate[len("```json\n") :]
        end_marker = "```"
    elif candidate.startswith("```\n"):
        inner = candidate[len("```\n") :]
        end_marker = "```"
    else:
        return None
    stripped = inner.rstrip()
    if not stripped.endswith(end_marker):
        raise ValueError("Unterminated JSON code fence")
    return stripped[: -len(end_marker)].strip()


_FENCE_BLOCK_RE = re.compile(
    r"```(?:json)?\s*\n(.*?)\n```",
    re.DOTALL,
)


def _last_fenced_json_blob(text: str) -> str | None:
    blocks = _FENCE_BLOCK_RE.findall(text)
    if not blocks:
        return None
    return blocks[-1].strip()


def _iter_closing_braces_outside_strings(text: str) -> list[int]:
    """Индексы `}` вне JSON-строк (одинарных и двойных кавычек)."""
    closes: list[int] = []
    in_string = False
    escape = False
    quote = ""
    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == quote:
                in_string = False
            continue
        if ch in ('"', "'"):
            in_string = True
            quote = ch
            continue
        if ch == "}":
            closes.append(i)
    return closes


def _matching_open_brace(text: str, close_idx: int) -> int | None:
    """Парная `{` для `}` в `close_idx`, скан назад с учётом строк."""
    depth = 0
    in_string = False
    escape = False
    quote = ""
    i = close_idx
    while i >= 0:
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                in_string = False
            i -= 1
            continue
        if ch == "}":
            depth += 1
        elif ch == "{":
            depth -= 1
            if depth == 0:
                return i
        elif ch in ('"', "'"):
            in_string = True
            quote = ch
        i -= 1
    return None


def _loads_root_object(slice_text: str) -> dict[str, object] | None:
    try:
        payload = json.loads(slice_text)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict):
        return payload
    return None


def _trailing_allows_root_object(rest: str) -> bool:
    """Хвост после `}`: допустим только prose/пусто, не продолжение JSON-структуры."""
    trimmed = rest.strip()
    if not trimmed:
        return True
    return trimmed[0] not in "[],:{}"


def _tail_root_object(text: str) -> str | None:
    for close_idx in reversed(_iter_closing_braces_outside_strings(text)):
        open_idx = _matching_open_brace(text, close_idx)
        if open_idx is None:
            continue
        if not _trailing_allows_root_object(text[close_idx + 1 :]):
            continue
        candidate = text[open_idx : close_idx + 1]
        if _loads_root_object(candidate) is not None:
            return candidate
    return None


def extract_agent_json_text(text: str) -> str:
    """Извлечь один JSON-объект из ответа агента (prose, fence, хвостовой `{…}`).

    Raises:
        ValueError: нет валидного корневого объекта или битый fence с начала.
    """
    candidate = _strip_outer(text)
    if not candidate:
        raise ValueError(_AGENT_JSON_NOT_FOUND)

    from_start = _unfence_from_start(candidate)
    if from_start is not None:
        if _loads_root_object(from_start) is not None:
            return from_start
        raise ValueError(_AGENT_JSON_NOT_FOUND)

    whole = _loads_root_object(candidate)
    if whole is not None:
        return candidate

    fenced = _last_fenced_json_blob(candidate)
    if fenced is not None and _loads_root_object(fenced) is not None:
        return fenced

    tail = _tail_root_object(candidate)
    if tail is not None:
        return tail

    raise ValueError(_AGENT_JSON_NOT_FOUND)


def _unwrap_agent_json(text: str) -> str:
    """Совместимость: делегирует в :func:`extract_agent_json_text`."""
    return extract_agent_json_text(text)


def is_agent_json_object(text: str) -> bool:
    """True, если из ``text`` извлекается один JSON-object (не prose/substring)."""
    candidate = text.strip()
    if not candidate:
        return False
    try:
        payload = json.loads(extract_agent_json_text(candidate))
    except (ValueError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict)


def parse_agent_json(text: str, model: type[TModel]) -> TModel:
    """Распарсить нормализованный JSON ответа и провалидировать ``model``."""
    return model.model_validate_json(extract_agent_json_text(text))
