"""Вырезки FAILURES / traceback pytest для handoff в test_triage."""

from __future__ import annotations

import re

from agents.verify.dispatch_unit.repro_cohort import pytest_node_id

_FAILURES_BEGIN = re.compile(r"^=+\s*FAILURES\s*=+", re.IGNORECASE)
_FAILURES_END = re.compile(
    r"^=+\s*(?:short test summary info|ERRORS|warnings summary)",
    re.IGNORECASE,
)
_TEST_BANNER = re.compile(r"^_{5,}\s+(?P<name>.+?)\s+_{5,}$")
_PYTEST_SUMMARY = re.compile(
    r"^(?:FAILED|ERROR)\s+(?P<node>[^\s]+?\.py(?:::[^\s]+)*)\s*-\s*(?P<reason>.*)$",
)
_E_LINE = re.compile(r"^E\s+(?P<message>.+)$")

# Per finding: полный блок падения; общий лимит защищает GraphState от раздувания.
DEFAULT_PYTEST_EXCERPT_MAX_CHARS = 24_000
_DEFAULT_TRUNCATION_SUFFIX = "\n… (pytest failure excerpt truncated)"


def _truncate_excerpt(text: str, *, limit: int) -> str:
    """Обрезать excerpt с явным маркером, не ломая UTF-8."""
    cleaned = text.strip()
    if not cleaned or len(cleaned) <= limit:
        return cleaned
    room = max(0, limit - len(_DEFAULT_TRUNCATION_SUFFIX))
    return cleaned[:room].rstrip() + _DEFAULT_TRUNCATION_SUFFIX


def _qualname_from_node(nodeid: str) -> str:
    return nodeid.split("::", 1)[-1].replace("\\", "/")


def _module_from_node(nodeid: str) -> str:
    return nodeid.split("::", 1)[0].replace("\\", "/")


def _banner_matches_node(banner: str, nodeid: str) -> bool:
    """Сопоставить underscore-banner pytest с node id."""
    qual = _qualname_from_node(nodeid)
    banner_norm = banner.strip()
    if banner_norm == qual:
        return True
    if banner_norm.startswith(qual) or qual.startswith(banner_norm):
        return True
    base_qual = qual.split("[", 1)[0]
    base_banner = banner_norm.split("[", 1)[0]
    if base_qual == base_banner and base_qual:
        return True
    return False


def _scan_failure_blocks(text: str) -> list[tuple[str, str]]:
    """Разобрать секцию FAILURES на пары (banner_name, block_text)."""
    lines = text.splitlines()
    in_failures = False
    blocks: list[tuple[str, str]] = []
    current_name: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_name, current_lines
        if current_name and current_lines:
            blocks.append((current_name, "\n".join(current_lines).strip()))
        current_name = None
        current_lines = []

    for line in lines:
        stripped = line.strip()
        if _FAILURES_BEGIN.match(stripped):
            in_failures = True
            flush()
            continue
        if in_failures and _FAILURES_END.match(stripped):
            flush()
            break
        if not in_failures:
            continue
        banner = _TEST_BANNER.match(stripped)
        if banner is not None:
            flush()
            current_name = banner.group("name").strip()
            current_lines = [line]
            continue
        if current_name is not None:
            current_lines.append(line)

    flush()
    return blocks


def _e_lines_tail(text: str, nodeid: str) -> str:
    """Запасной excerpt: summary FAILED + ближайшие ``E`` строки для node."""
    lines = text.splitlines()
    summary_parts: list[str] = []
    e_parts: list[str] = []
    qual = _qualname_from_node(nodeid)
    for raw in lines:
        stripped = raw.strip()
        sm = _PYTEST_SUMMARY.match(stripped)
        if sm is not None and sm.group("node").replace("\\", "/") == nodeid.replace("\\", "/"):
            summary_parts.append(stripped)
            continue
        em = _E_LINE.match(stripped)
        if em is not None:
            e_parts.append(stripped)
    if not summary_parts and not e_parts:
        return ""
    header = f"# pytest node: {nodeid}"
    body = "\n".join(summary_parts + e_parts[-40:])
    if qual and qual not in body:
        body = f"# qualname: {qual}\n{body}"
    return f"{header}\n{body}".strip()


def _block_for_node(text: str, nodeid: str, blocks: list[tuple[str, str]]) -> str:
    """Найти FAILURES block для node id."""
    module = _module_from_node(nodeid)
    for banner, block in blocks:
        if not _banner_matches_node(banner, nodeid):
            continue
        if module.split("/")[-1] in block or module in block:
            return block
        # Имя совпало, путь в tb может отсутствовать у короткого tb
        return block
    for banner, block in blocks:
        if _banner_matches_node(banner, nodeid):
            return block
    return ""


def build_pytest_failure_excerpts(
    text: str,
    node_ids: list[str],
    *,
    max_chars: int = DEFAULT_PYTEST_EXCERPT_MAX_CHARS,
) -> dict[str, str]:
    """Построить excerpt падения pytest для каждого node id.

    Args:
        text: полный stdout+stderr прогона pytest (не log tail).
        node_ids: нормализованные node id упавших тестов.
        max_chars: лимит символов на один excerpt.

    Returns:
        Словарь node id → текст FAILURES / traceback.
    """
    if not text.strip() or not node_ids:
        return {}
    blocks = _scan_failure_blocks(text)
    excerpts: dict[str, str] = {}
    for node in node_ids:
        normalized = node.replace("\\", "/")
        piece = _block_for_node(text, normalized, blocks)
        if not piece:
            piece = _e_lines_tail(text, normalized)
        if not piece:
            continue
        prefixed = f"pytest node id: {normalized}\n\n{piece}"
        excerpts[normalized] = _truncate_excerpt(prefixed, limit=max_chars)
    return excerpts


def node_id_from_pytest_observation(observation: str) -> str | None:
    """Node id из строки ``quality_gate_diagnostics.pytest``."""
    node = pytest_node_id(observation)
    if node is None:
        return None
    return node.replace("\\", "/")


def lookup_pytest_failure_excerpt(
    diagnostics: dict[str, object] | None,
    *,
    observation: str,
) -> str | None:
    """Взять excerpt из ``quality_gate_diagnostics.pytest_failure_excerpts``."""
    if not isinstance(diagnostics, dict):
        return None
    raw_map = diagnostics.get("pytest_failure_excerpts")
    if not isinstance(raw_map, dict):
        return None
    node = node_id_from_pytest_observation(observation)
    if node is None:
        return None
    value = raw_map.get(node)
    if not isinstance(value, str) or not value.strip():
        return None
    return value
