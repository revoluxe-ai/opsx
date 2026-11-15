# -*- coding: utf-8 -*-
"""Атомарная публикация JSON-артефактов bounded-verify (Protocol A).

Temp sibling → flush/fsync → schema validate → os.replace.
Читатели игнорируют partial temp; malformed canonical не принимается.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, MutableMapping, Optional

PLAN_SCHEMA_VERSION = "bounded-verify-plan-v1"


def canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    """Сериализует payload в детерминированный canonical JSON (UTF-8)."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def read_canonical_json(path: Path) -> Optional[MutableMapping[str, Any]]:
    """Читает canonical JSON-файл; None если отсутствует или невалиден."""
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        parsed = json.loads(text)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


def is_temp_sibling(path: Path) -> bool:
    """True если имя файла — temp sibling (plan.json.tmp.<pid>.<nonce>)."""
    name = path.name
    return ".tmp." in name


def is_canonical_plan_readable(plan_path: Path) -> bool:
    """True если canonical plan.json существует и парсится как object."""
    return read_canonical_json(plan_path) is not None


def atomic_publish_json(
    target_path: Path,
    payload: Mapping[str, Any],
    *,
    validate: Callable[[Mapping[str, Any]], None],
    test_hook: Optional[Mapping[str, Any]] = None,
) -> None:
    """Атомарно публикует JSON через temp sibling и os.replace.

    Args:
        target_path: Canonical path (e.g. plan.json).
        payload: Полный документ для публикации.
        validate: Callback schema/business validation перед replace.
        test_hook: Тестовый хук; ``crash_before_replace`` пропускает replace.

  Raises:
        ValueError: validate callback rejected payload.
        OSError: I/O failure.
    """
    target_path.parent.mkdir(parents=True, exist_ok=True)
    nonce = uuid.uuid4().hex[:12]
    temp_path = target_path.with_name(
        f"{target_path.name}.tmp.{os.getpid()}.{nonce}"
    )
    body = canonical_json_bytes(payload)
    temp_path.write_bytes(body)
    with temp_path.open("r+b") as handle:
        handle.flush()
        os.fsync(handle.fileno())

    parsed = json.loads(body.decode("utf-8"))
    validate(parsed)

    if test_hook and test_hook.get("crash_before_replace"):
        return

    os.replace(temp_path, target_path)


def list_temp_siblings(directory: Path, base_name: str) -> list[Path]:
    """Возвращает temp siblings для canonical имени (для диагностики/тестов)."""
    prefix = f"{base_name}.tmp."
    return sorted(
        p for p in directory.iterdir() if p.is_file() and p.name.startswith(prefix)
    )
