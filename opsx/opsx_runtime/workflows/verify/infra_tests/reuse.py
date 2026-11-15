"""Повторное использование результата узла, если digest теста и его импортов не менялся."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NodeReuseRecord:
    """Снимок, с которым сравнивается текущий прогон."""

    node_id: str
    file_digest: str
    closure_digest: str
    status: str


def reuse_eligible(record: NodeReuseRecord, *, file_digest: str, closure_digest: str) -> bool:
    """True, когда оба digest совпали и прошлый статус был зелёным."""
    if record.status not in {"PASS", "PASS_NO_SCOPE"}:
        return False
    return record.file_digest == file_digest and record.closure_digest == closure_digest
