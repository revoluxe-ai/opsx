"""Доставка PNG модели через paged MCP content (не base64 в промпте)."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_PAGE = 48_000


@dataclass(frozen=True, slots=True)
class PagedImageChunk:
    """Одна страница бинарного payload."""

    path: str
    offset: int
    total_bytes: int
    chunk_b64: str
    mime: str = "image/png"
    eof: bool = False


class ImageModelInputPort:
    """Читает allowlisted PNG и отдаёт страницы для custom tool."""

    def __init__(self, *, repo_root: Path, page_size: int = _DEFAULT_PAGE) -> None:
        self._repo_root = repo_root.resolve()
        self._page_size = max(256, int(page_size))

    def resolve_path(self, relative: str) -> Path:
        path = (self._repo_root / relative).resolve()
        if not str(path).startswith(str(self._repo_root)):
            msg = "path outside repository"
            raise ValueError(msg)
        return path

    def page_image(self, relative: str, *, offset: int = 0) -> PagedImageChunk:
        path = self.resolve_path(relative)
        if not path.is_file():
            msg = f"missing image: {relative}"
            raise FileNotFoundError(msg)
        data = path.read_bytes()
        total = len(data)
        off = max(0, int(offset))
        chunk = data[off : off + self._page_size]
        eof = off + len(chunk) >= total
        return PagedImageChunk(
            path=relative.replace("\\", "/"),
            offset=off,
            total_bytes=total,
            chunk_b64=base64.b64encode(chunk).decode("ascii"),
            eof=eof,
        )
