"""Разбор результата infra-прогона: junit, skip=fail, circuit breaker, сигнатуры.

Модуль не запускает pytest и не импортирует продуктовые тесты.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass


_SIGNATURES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"connection refused|could not connect", re.I), "ENV"),
    (re.compile(r"alembic|Can't locate revision", re.I), "ENV"),
    (re.compile(r"fixture .* not found|ScopeMismatch", re.I), "TEST_HARNESS"),
    (re.compile(r"AssertionError|E\s+assert ", re.I), "PRODUCT_BUG"),
)


@dataclass(frozen=True)
class RunSummary:
    """Итог одного прогона по junit."""

    passed: int
    failed: int
    skipped: int
    status: str
    owner: str


def summarize_junit(xml_text: str) -> RunSummary:
    """Skip в infra-прогоне — FAIL ``SKIP_AS_PASS``, не успех."""
    root = ET.fromstring(xml_text)
    passed = failed = skipped = 0
    blobs: list[str] = []
    for case in root.iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            failed += 1
            node = case.find("failure")
            if node is None:
                node = case.find("error")
            blobs.append("".join(node.itertext()) if node is not None else "")
        elif case.find("skipped") is not None:
            skipped += 1
        else:
            passed += 1
    if skipped:
        return RunSummary(passed, failed, skipped, "FAIL", "SKIP_AS_PASS")
    if failed:
        return RunSummary(passed, failed, skipped, "FAIL", classify_failure("\n".join(blobs)))
    return RunSummary(passed, failed, skipped, "PASS", "")


def classify_failure(text: str) -> str:
    """Таблица сигнатур. Неизвестное остаётся UNKNOWN и уходит агенту."""
    for pattern, owner in _SIGNATURES:
        if pattern.search(text):
            return owner
    return "UNKNOWN"


def circuit_open(signatures: tuple[str, ...], *, threshold: int = 3) -> bool:
    """Одинаковая env-сигнатура threshold раз подряд останавливает прогон."""
    if len(signatures) < threshold:
        return False
    tail = signatures[-threshold:]
    return len(set(tail)) == 1 and tail[0].startswith("ENV")


def confirm_class(*, failed_in_parallel: bool, passed_in_isolation: bool) -> str:
    """Упал в parallel и прошёл в isolation — дефект изоляции, иначе детерминированный."""
    if failed_in_parallel and passed_in_isolation:
        return "ISOLATION_DEFECT"
    return "DETERMINISTIC"
