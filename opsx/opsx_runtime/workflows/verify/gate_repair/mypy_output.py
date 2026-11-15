"""Разбор полного вывода mypy и оценка полноты diagnostics (без обрезки log tail)."""

from __future__ import annotations

import re
from typing import Literal

from workflows.verify.gate_repair.schemas import GateCheckResult

InputIntegrityStatus = Literal["COMPLETE", "PARTIAL", "INVALID"]

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9?]*[ -/]*[@-~]")
_TRUNCATION_BANNER = re.compile(r"^\u2026\s*\[truncated", re.IGNORECASE)
_MYPY_ERROR_LINE = re.compile(
    r"^(?P<head>.+?):(?P<line>\d+)(?::\d+)?:\s+error:\s+(?P<message>.+)$",
)
_MYPY_NOTE_LINE = re.compile(
    r"^(?P<head>.+?):(?P<line>\d+)(?::\d+)?:\s+note:\s+(?P<message>.+)$",
)
_MYPY_FOUND_ERRORS = re.compile(
    r"Found\s+(?P<count>\d+)\s+error(?:s)?\s+in\s+\d+\s+file",
    re.IGNORECASE,
)
_MYPY_SUCCESS = re.compile(r"Success:\s+no issues found", re.IGNORECASE)


def strip_ansi(text: str) -> str:
    """Убрать ANSI escape-последовательности из строки mypy."""
    return _ANSI_ESCAPE.sub("", text)


def normalize_mypy_log_text(text: str) -> str:
    """Подготовить текст лога: ANSI off, без баннера обрезки tail."""
    lines: list[str] = []
    for raw in text.splitlines():
        line = strip_ansi(raw.rstrip("\r"))
        if _TRUNCATION_BANNER.match(line.strip()):
            continue
        lines.append(line)
    return "\n".join(lines)


def _is_continuation_line(stripped: str) -> bool:
    """Строка продолжения multiline diagnostic (без собственного location)."""
    if not stripped:
        return False
    if stripped.startswith("note:"):
        return True
    if stripped[0] in {'"', "^", " "}:
        return True
    if stripped.startswith("[") and "]" in stripped[:20]:
        return True
    if ": error:" in stripped or ": note:" in stripped:
        return False
    return not re.match(r"^[\w./\\-]+:\d+", stripped)


def parse_mypy_issues(text: str) -> list[str]:
    """Собрать каждую error-строку mypy из **полного** (или tail) log text.

    ``note:`` и multiline-продолжения приклеиваются к последней error-строке.
    Дедупликация только при совпадении (path, line, first-line message).
    """
    issues: list[str] = []
    seen_keys: set[tuple[str, int, str]] = set()
    normalized = normalize_mypy_log_text(text)
    for raw_line in normalized.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        error_match = _MYPY_ERROR_LINE.match(stripped)
        if error_match:
            path = error_match.group("head").strip()
            line_no = int(error_match.group("line"))
            message = error_match.group("message").strip()
            key = (path, line_no, message)
            formatted = f"{path}:{line_no}: {message}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            issues.append(formatted)
            continue
        note_match = _MYPY_NOTE_LINE.match(stripped)
        if note_match and issues:
            note_msg = note_match.group("message").strip()
            issues[-1] = f"{issues[-1]} (note: {note_msg})"
            continue
        if stripped.startswith("note:") and issues:
            issues[-1] = f"{issues[-1]} (note: {stripped[5:].strip()})"
            continue
        if issues and _is_continuation_line(stripped):
            issues[-1] = f"{issues[-1]} {stripped}"
    return issues


def parse_mypy_reported_error_count(text: str) -> int | None:
    """Число ошибок из summary ``Found N error(s)…`` или ``Success: no issues found``."""
    normalized = normalize_mypy_log_text(text)
    for line in reversed(normalized.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        if _MYPY_SUCCESS.search(stripped):
            return 0
        match = _MYPY_FOUND_ERRORS.search(stripped)
        if match:
            return int(match.group("count"))
    return None


def mypy_success_summary_present(text: str) -> bool:
    """Есть ли в логе подтверждение успешного прогона без ошибок."""
    normalized = normalize_mypy_log_text(text)
    for line in normalized.splitlines():
        if _MYPY_SUCCESS.search(line):
            return True
    return parse_mypy_reported_error_count(normalized) == 0


def assess_mypy_input_integrity(
    check: GateCheckResult,
    *,
    parsed_count: int,
    reported: int | None,
    log_text: str,
    raw_parsed_count: int | None = None,
) -> tuple[InputIntegrityStatus, list[str]]:
    """Детерминированная полнота mypy diagnostics для handoff / GroupErrorAgent.

    Args:
        parsed_count: Число error-строк в **product static scope** (handoff ``mypy``).
        raw_parsed_count: Все распарсенные error-строки до scope-фильтра; при совпадении
            с ``reported`` integrity остаётся COMPLETE даже если ``parsed_count`` меньше
            (handoff ``mypy`` — только ``src/stroy/``, summary mypy считает все ошибки).
    """
    issues: list[str] = []
    if check.error:
        return "INVALID", [f"mypy gate subprocess error: {check.error}"]
    if check.exit_code == 124:
        return "INVALID", ["mypy gate timed out"]
    if check.exit_code == 127 and not (check.stdout_tail or "").strip():
        detail = (check.stderr_tail or "command failed")[:240]
        return "INVALID", [f"mypy gate failed to execute: {detail}"]

    if check.exit_code == 0:
        if parsed_count != 0:
            issues.append("mypy exit 0 but parsed issue lines present")
            return "PARTIAL", issues
        if reported is not None and reported != 0:
            issues.append(f"mypy exit 0 but summary reports {reported} error(s)")
            return "PARTIAL", issues
        if reported is None and not mypy_success_summary_present(log_text):
            issues.append(
                "mypy exit 0 but success summary missing; completeness not provable",
            )
            return "PARTIAL", issues
        return "COMPLETE", []

    if reported is None:
        issues.append(
            "mypy summary line missing in log; cannot verify parsed diagnostics completeness",
        )
        return "PARTIAL", issues
    if reported != parsed_count:
        if raw_parsed_count is not None and raw_parsed_count == reported:
            return "COMPLETE", []
        issues.append(
            f"mypy_reported_error_count={reported} != mypy_parsed_issue_count={parsed_count}",
        )
        return "PARTIAL", issues
    return "COMPLETE", []


def synthesize_mypy_log(
    *,
    error_lines: list[str],
    reported_count: int | None = None,
) -> str:
    """Сгенерировать типичный stdout mypy для тестов (path:line: error: … + summary)."""
    body = "\n".join(error_lines)
    count = reported_count if reported_count is not None else len(error_lines)
    if count == 0:
        suffix = "Success: no issues found in 1 source file"
    else:
        suffix = f"Found {count} error{'s' if count != 1 else ''} in {count} file(s)"
    return f"{body}\n{suffix}" if body else suffix
