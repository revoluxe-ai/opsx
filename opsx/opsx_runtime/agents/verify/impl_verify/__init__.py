"""Публичные схемы ImplVerify без преждевременного импорта live SDK.

VerifyImplAnswer доступен offline; ImplVerifyAgent загружается по запросу.
"""
from __future__ import annotations

from .schemas import VerifyImplAnswer

__all__ = ["ImplVerifyAgent", "VerifyImplAnswer"]


def __getattr__(name: str):
    # Сохраняет публичный импорт класса, но не тянет .agent при чтении схем.
    if name == "ImplVerifyAgent":
        from .agent import ImplVerifyAgent
        return ImplVerifyAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
