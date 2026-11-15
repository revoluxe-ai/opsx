"""Счётчики IMPLEMENTATION_TRACK для tasks.md (контракт TASK_GENERATION §0).

Только чекбоксы вне секции ``## Post-implementation`` входят в apply-track.
"""

from __future__ import annotations

import re

_TASK_OPEN = re.compile(r"^\s*-\s*\[\s\]\s+")
_TASK_DONE = re.compile(r"^\s*-\s*\[[xX]\]\s+")
_POST_IMPL_HEADER = re.compile(r"^##\s+Post-implementation\b", re.IGNORECASE)


def count_implementation_track(tasks_text: str | None) -> dict[str, int | str]:
    """Подсчитать open/done чекбоксы в IMPLEMENTATION_TRACK.

    Args:
        tasks_text: Содержимое ``tasks.md`` или None.

    Returns:
        ``total``, ``completed``, ``remaining``, ``scope`` = IMPLEMENTATION_TRACK.
    """
    if not tasks_text:
        return {
            "total": 0,
            "completed": 0,
            "remaining": 0,
            "scope": "IMPLEMENTATION_TRACK",
        }
    in_post_impl = False
    completed = 0
    remaining = 0
    for line in tasks_text.splitlines():
        stripped = line.strip()
        if _POST_IMPL_HEADER.match(stripped):
            in_post_impl = True
            continue
        if in_post_impl:
            continue
        if _TASK_OPEN.match(line):
            remaining += 1
        elif _TASK_DONE.match(line):
            completed += 1
    total = completed + remaining
    return {
        "total": total,
        "completed": completed,
        "remaining": remaining,
        "scope": "IMPLEMENTATION_TRACK",
    }
