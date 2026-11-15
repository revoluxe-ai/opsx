"""Сопоставление путей с include/exclude pyright (общий с bounded-verify coverage)."""

from __future__ import annotations

import fnmatch
from typing import Sequence


def matches_glob(path: str, pattern: str) -> bool:
    """Проверяет, попадает ли repo-relative путь под glob/include entry."""
    norm = path.replace("\\", "/").lstrip("./")
    pat = pattern.replace("\\", "/").lstrip("./")
    if pat.endswith("/"):
        pat = pat.rstrip("/")
    if norm == pat or norm.startswith(pat + "/"):
        return True
    return fnmatch.fnmatch(norm, pat)


def path_in_include(path: str, include: Sequence[str]) -> bool:
    return any(matches_glob(path, inc) for inc in include)


def path_hit_exclude(path: str, exclude: Sequence[str]) -> bool:
    return any(matches_glob(path, ex) for ex in exclude)
