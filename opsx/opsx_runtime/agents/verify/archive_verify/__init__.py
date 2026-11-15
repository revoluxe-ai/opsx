"""Публичные схемы ArchiveVerify без преждевременного импорта live SDK.

VerifyArchiveAnswer доступен offline; ArchiveVerifyAgent загружается по запросу.
"""

from __future__ import annotations

from .schemas import VerifyArchiveAnswer

__all__ = ["ArchiveVerifyAgent", "VerifyArchiveAnswer"]


def __getattr__(name: str):
    if name == "ArchiveVerifyAgent":
        from .agent import ArchiveVerifyAgent

        return ArchiveVerifyAgent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
