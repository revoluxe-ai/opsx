"""Cursor SDK retains the original repository workspace unchanged."""
from __future__ import annotations
from pathlib import Path
from typing import Any


class CursorExecutionWorkspace:
    def __init__(self, *, source: Path, run_id: str) -> None:
        self.source = Path(source).resolve()
        if not run_id:
            raise RuntimeError("EXECUTION_WORKSPACE_IDENTITY_MISSING")

    async def __aenter__(self) -> Path:
        return self.source

    async def __aexit__(self, *_exc: Any) -> None:
        return None
