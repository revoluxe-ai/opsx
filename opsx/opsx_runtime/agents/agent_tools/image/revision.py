"""Идентичность байтов output для repair/idempotency."""

from __future__ import annotations

import hashlib
from pathlib import Path


def output_file_revision(path: Path) -> str:
    """SHA-256 содержимого файла."""
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest()
