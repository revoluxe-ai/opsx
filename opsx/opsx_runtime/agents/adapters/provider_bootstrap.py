"""Discover and import the adapter provider package (neutral composition hook)."""

from __future__ import annotations

import importlib
import os
from pathlib import Path

from agents.adapters.provider_registry import (
    call_bootstrap_hook,
    factories_registered,
)

_PROVIDER_MARKER = "register_review_adapter_factory"


def ensure_adapter_factories_registered() -> None:
    """Load provider adapter package so registry factories and reconcile hooks exist."""
    if factories_registered():
        return
    selected = os.environ.get("OPSX_ADAPTER_MODULE", "").strip()
    if selected:
        importlib.import_module(selected)
        if not factories_registered():
            raise RuntimeError(f"configured adapter {selected!r} did not register factories")
        return
    adapters_root = Path(__file__).resolve().parent
    for entry in sorted(adapters_root.iterdir()):
        if not entry.is_dir() or entry.name.startswith("_"):
            continue
        init_py = entry / "__init__.py"
        if not init_py.is_file():
            continue
        try:
            marker = init_py.read_text(encoding="utf-8")
        except OSError:
            continue
        if _PROVIDER_MARKER not in marker:
            continue
        importlib.import_module(f"agents.adapters.{entry.name}")
        if factories_registered():
            return
    raise RuntimeError("adapter provider package not found or did not register factories")


def ensure_provider_wheel_importable(*, repo_root: Path | None = None) -> None:
    """Load provider package and connect its wheel without naming the provider in callers."""
    ensure_adapter_factories_registered()
    call_bootstrap_hook("ensure_wheel_importable", repo_root=repo_root)
