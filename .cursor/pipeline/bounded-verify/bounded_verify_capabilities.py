# -*- coding: utf-8 -*-
"""Чтение TARGET product/test roots из ``opsx/project/capabilities.yaml``.

Fail-closed при отсутствии или пустых ``source_roots`` / ``test_roots``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

import yaml

from bounded_verify_plan import BoundedVerifyPlanError

REPO_ROOT = Path(__file__).resolve().parents[3]
CAPABILITIES_YAML = REPO_ROOT / "opsx" / "project" / "capabilities.yaml"

TARGET_CAPABILITY_UNRESOLVED = "TARGET_CAPABILITY_UNRESOLVED"


def _normalize_root(raw: str) -> str:
    value = str(raw).replace("\\", "/").strip().lstrip("./")
    if not value:
        raise BoundedVerifyPlanError(
            TARGET_CAPABILITY_UNRESOLVED,
            "empty capability root entry",
        )
    if not value.endswith("/"):
        value += "/"
    return value


def _roots_from_env(key: str) -> tuple[str, ...] | None:
    """Тестовый override: ``OPSX_BOUNDED_VERIFY_SOURCE_ROOTS`` / ``TEST_ROOTS`` (``;``-sep)."""
    env_key = f"OPSX_BOUNDED_VERIFY_{key}"
    raw = os.environ.get(env_key)
    if not raw:
        return None
    out: list[str] = []
    for part in raw.split(";"):
        part = part.strip()
        if part:
            norm = _normalize_root(part)
            if norm not in out:
                out.append(norm)
    return tuple(out)


def load_capabilities_document(*, root: Path = REPO_ROOT) -> Mapping[str, Any]:
    """Загружает capabilities YAML; отказывает если файл отсутствует."""
    path = root / "opsx" / "project" / "capabilities.yaml"
    if not path.is_file():
        raise BoundedVerifyPlanError(
            TARGET_CAPABILITY_UNRESOLVED,
            f"missing capabilities.yaml: {path}",
        )
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise BoundedVerifyPlanError(
            TARGET_CAPABILITY_UNRESOLVED,
            "capabilities.yaml is not a mapping",
        )
    return data


def product_source_roots(*, root: Path = REPO_ROOT) -> tuple[str, ...]:
    """Нормализованные префиксы product source (например ``src/stroy/``)."""
    env = _roots_from_env("SOURCE_ROOTS")
    if env is not None:
        return env
    caps = load_capabilities_document(root=root)
    product = caps.get("product")
    if not isinstance(product, dict):
        raise BoundedVerifyPlanError(
            TARGET_CAPABILITY_UNRESOLVED,
            "capabilities.product missing",
        )
    raw_roots = product.get("source_roots")
    if not isinstance(raw_roots, list) or not raw_roots:
        raise BoundedVerifyPlanError(
            TARGET_CAPABILITY_UNRESOLVED,
            "product.source_roots empty or missing",
        )
    return tuple(_normalize_root(str(r)) for r in raw_roots)


def product_test_roots(*, root: Path = REPO_ROOT) -> tuple[str, ...]:
    """Нормализованные префиксы test roots (например ``tests/unit/``)."""
    env = _roots_from_env("TEST_ROOTS")
    if env is not None:
        return env
    caps = load_capabilities_document(root=root)
    product = caps.get("product")
    if not isinstance(product, dict):
        raise BoundedVerifyPlanError(
            TARGET_CAPABILITY_UNRESOLVED,
            "capabilities.product missing",
        )
    raw_roots = product.get("test_roots")
    if not isinstance(raw_roots, list) or not raw_roots:
        raise BoundedVerifyPlanError(
            TARGET_CAPABILITY_UNRESOLVED,
            "product.test_roots empty or missing",
        )
    return tuple(_normalize_root(str(r)) for r in raw_roots)


def require_product_test_roots(*, root: Path = REPO_ROOT) -> tuple[str, ...]:
    """Объединённые префиксы; fail-closed если оба набора пусты после resolve."""
    sources = product_source_roots(root=root)
    tests = product_test_roots(root=root)
    if not sources and not tests:
        raise BoundedVerifyPlanError(
            TARGET_CAPABILITY_UNRESOLVED,
            "no product or test roots resolved",
        )
    return sources + tests
