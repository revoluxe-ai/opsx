"""Привести аргументы ``submit_apply_design`` к строке JSON до handler.

Провайдер и роль разрешают ``content`` объектом. Директива ``$from_path``
может содержать другие ключи, включая написанный моделью
``artifact_identity.artifact_sha256``. Такой вызов — обычный patch канона,
а не ``INVALID_ARGS``. ``artifact_sha256`` связывает review с байтами файла
и в документ не пишется.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from opsx.project_context import resource_path

_FROM_PATH_KEY = "$from_path"
_RECOMPUTE_IDENTITIES_KEY = "$recompute_identities"
_DIRECTIVE_KEYS = frozenset({_FROM_PATH_KEY, _RECOMPUTE_IDENTITIES_KEY})


def _deep_merge(base: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in patch.items():
        if key in _DIRECTIVE_KEYS:
            continue
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge(current, value)
            continue
        merged[key] = value
    return merged


def _strip_authored_artifact_sha256(body: Mapping[str, Any]) -> dict[str, Any]:
    cleaned = dict(body)
    cleaned.pop("artifact_sha256", None)
    identity = cleaned.get("artifact_identity")
    if isinstance(identity, dict) and "artifact_sha256" in identity:
        kept = dict(identity)
        kept.pop("artifact_sha256", None)
        cleaned["artifact_identity"] = kept
    return cleaned


def _canonical_target(repo: Path, raw_path: str, target: Path) -> Path | None:
    rel = raw_path.strip().replace("\\", "/").lstrip("/")
    if not rel:
        return None
    candidate = resource_path(repo, rel).resolve()
    try:
        candidate.relative_to(repo.resolve())
    except ValueError:
        return None
    if candidate != target.resolve():
        return None
    if not candidate.is_file():
        return None
    return candidate


def _load_json_object(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def coerce_submit_content(content: Any, *, repo: Path, target: Path) -> Any:
    """Вернуть JSON-строку, которую handler принимает для любого change.

    Объект становится строкой. ``$from_path`` вместе с patch сливается в
    канонический файл; ``artifact_sha256`` снимается. Чужой путь не
    подменяется: handler сам вернёт ``FORBIDDEN_PATH``.
    """
    if isinstance(content, (dict, list)):
        text = json.dumps(content)
    elif isinstance(content, str):
        text = content
    else:
        return content
    body = _load_json_object(text)
    if body is None or _FROM_PATH_KEY not in body:
        if body is None:
            return text
        cleaned = _strip_authored_artifact_sha256(body)
        return json.dumps(cleaned)
    raw_path = body.get(_FROM_PATH_KEY)
    if not isinstance(raw_path, str):
        return text
    source = _canonical_target(repo, raw_path, target)
    if source is None:
        return text
    loaded = _load_json_object(source.read_text(encoding="utf-8"))
    if loaded is None:
        return text
    patch = {key: value for key, value in body.items() if key not in _DIRECTIVE_KEYS}
    merged = _deep_merge(loaded, patch) if patch else dict(loaded)
    merged = _strip_authored_artifact_sha256(merged)
    merged[_RECOMPUTE_IDENTITIES_KEY] = True
    merged.pop(_FROM_PATH_KEY, None)
    return json.dumps(merged)


async def normalize_submit_apply_design_args(
    runtime: Any,
    args: Mapping[str, Any],
) -> dict[str, Any]:
    """Скопировать args и подставить уже приведённый ``content``."""
    prepared = dict(args)
    submission = getattr(getattr(runtime, "_manifest", None), "design_submission", None)
    repo = getattr(runtime, "_repo", None)
    target = getattr(submission, "json_path", None)
    if not isinstance(repo, Path) or not isinstance(target, Path):
        content = prepared.get("content")
        if isinstance(content, (dict, list)):
            prepared["content"] = json.dumps(content)
        return prepared
    prepared["content"] = coerce_submit_content(
        prepared.get("content"),
        repo=repo,
        target=target,
    )
    return prepared
