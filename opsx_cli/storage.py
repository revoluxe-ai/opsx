"""Async file operations and atomic metadata publication."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
from typing import Any


def _atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".opsx-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


async def write_bytes(path: Path, content: bytes) -> None:
    await asyncio.to_thread(_atomic_bytes, path, content)


async def write_json(path: Path, value: Any) -> None:
    await write_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


async def read_json(path: Path, default: Any = None) -> Any:
    def read() -> Any:
        if not path.is_file():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    return await asyncio.to_thread(read)
