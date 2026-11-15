"""Parse review and repair agent outputs into structured results.

Bugbot requires `REVIEW_SCOPE: FULL_BRANCH_DIFF`. Security review follows the
Cursor `/review-security` summary contract (no scope header). Repair parsers
share STATUS-token extraction; security adds `SECURITY_BATCH_CLEAR`.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Iterable

from finding_filter import partition_repair_findings
from models import RepairRunResult, ReviewRunResult

_TABLE_ROW_RE = re.compile(
    r"^\|\s*(?P<severity>[^|]+?)\s*\|\s*(?P<location>[^|]+?)\s*\|\s*(?P<finding>[^|]+?)\s*\|",
    re.MULTILINE,
)

_CLEAN_PATTERNS = (
    re.compile(r"bugbot found no bugs", re.IGNORECASE),
    re.compile(r"bugbot found 0 findings", re.IGNORECASE),
    re.compile(r"found no bugs\b", re.IGNORECASE),
    re.compile(r"found no issues\b", re.IGNORECASE),
    re.compile(r"found no findings\b", re.IGNORECASE),
    re.compile(r"no actionable findings", re.IGNORECASE),
    re.compile(r"there was no diff to review", re.IGNORECASE),
    re.compile(r"^no diff to review\b", re.IGNORECASE | re.MULTILINE),
)

_REVIEW_FAILED_MARKERS = (
    "не стартовал",
    "did not start",
    "could not start",
    "review failed",
    "review blocked",
    "blocked by",
    "pretooluse",
    "pre-tool hook",
    "pretool hook",
    "shell недоступен",
    "shell is not available",
    "shell is blocked",
    "shell blocked",
    "tool calls",
    "режет сломанный",
    "unable to run",
    "cannot run",
    "не удалось запустить",
    "integration gap",
)

_SECURITY_BRANCH_DIFF_BLOCKER_MARKERS = (
    "could not compute the branch-changes diff",
    "could not compute the branch diff",
    "could not compute branch-changes",
    "could not compute branch changes",
    "failed to compute the branch-changes diff",
    "failed to compute the branch diff",
)

_REVIEW_DEGRADED_MARKERS = (
    "working tree review",
    "working-tree review",
    "not a full git diff",
    "not a full `git diff`",
    "dirty slice",
    "dirty charter slice",
    "only dirty",
    "without shell",
    "хук заблокировал",
    "blocked by a broken hook",
    "blocked composite command",
    "составную команду",
    "degraded review",
    "partial review",
    "could not run git diff",
    "could not compute the branch",
    "could not compute the branch-changes",
)

_REVIEW_SCOPE_OK = re.compile(
    r"^REVIEW_SCOPE:\s*FULL_BRANCH_DIFF\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Match infra/CLI failures, not product prose such as "timeout/fail-closed".
_TRANSIENT_PATTERNS = (
    re.compile(r"connection lost", re.IGNORECASE),
    re.compile(r"connection reset", re.IGNORECASE),
    re.compile(r"\beconnreset\b", re.IGNORECASE),
    re.compile(r"\betimedout\b", re.IGNORECASE),
    re.compile(
        r"(?:request|connection|read|write|operation|socket|cli)\s+timeout",
        re.IGNORECASE,
    ),
    re.compile(r"timed\s+out", re.IGNORECASE),
    re.compile(r"timeout\s+(?:error|expired|reached|exceeded)", re.IGNORECASE),
    re.compile(r"temporarily unavailable", re.IGNORECASE),
    re.compile(r"service unavailable", re.IGNORECASE),
    re.compile(r"rate limit", re.IGNORECASE),
    re.compile(r"\btransient\s+(?:error|failure|issue)\b", re.IGNORECASE),
    re.compile(r"cli connection", re.IGNORECASE),
    re.compile(r"corrupt local agent checkpoint", re.IGNORECASE),
)


def _normalize_cell(value: str) -> str:
    return " ".join(value.strip().split())


def _normalize_finding_row(row: dict[str, str]) -> dict[str, str]:
    return {
        "severity": _normalize_cell(row["severity"]).lower(),
        "location": _normalize_cell(row["location"]).lower(),
        "finding": _normalize_cell(row["finding"]).lower(),
    }


def extract_findings_table(raw_output: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for match in _TABLE_ROW_RE.finditer(raw_output):
        severity = _normalize_cell(match.group("severity"))
        location = _normalize_cell(match.group("location"))
        finding = _normalize_cell(match.group("finding"))
        if severity.lower() in {"severity", "---", "----"}:
            continue
        if set(severity) <= {"-"}:
            continue
        findings.append(
            {
                "severity": severity,
                "location": location,
                "finding": finding,
            }
        )
    return findings


def build_fingerprint(findings: Iterable[dict[str, str]]) -> str | None:
    normalized = [_normalize_finding_row(row) for row in findings]
    if not normalized:
        return None
    normalized.sort(key=lambda row: (row["severity"], row["location"], row["finding"]))
    payload = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def is_transient_error(raw_output: str) -> bool:
    return any(pattern.search(raw_output) for pattern in _TRANSIENT_PATTERNS)


def has_security_preflight_fallback_summary(raw_output: str) -> bool:
    """True when parent agent completed artifact FALLBACK with required summary."""
    lowered = raw_output.lower()
    if "fallback:" in lowered or "fallback —" in lowered:
        return True
    if any(pattern.search(raw_output) for pattern in _CLEAN_PATTERNS):
        return True
    if "security review found" in lowered and "findings" in lowered:
        return True
    if extract_findings_table(raw_output):
        return True
    return False


def is_security_preflight_blocker_only(raw_output: str) -> bool:
    """Subagent branch-diff blocker without artifact FALLBACK summary."""
    if not raw_output.strip():
        return False
    lowered = raw_output.lower()
    if not any(marker in lowered for marker in _SECURITY_BRANCH_DIFF_BLOCKER_MARKERS):
        return False
    return not has_security_preflight_fallback_summary(raw_output)


def is_review_failed(
    raw_output: str,
    *,
    git_preflight_ok: bool = False,
    require_scope: bool = True,
) -> bool:
    if not raw_output.strip():
        return True
    if git_preflight_ok:
        # POC already verified branch diff; ignore agent hook/shell narratives.
        if extract_findings_table(raw_output):
            return False
        if any(pattern.search(raw_output) for pattern in _CLEAN_PATTERNS):
            return False
        if "0 findings" in raw_output.lower():
            return False
        if not require_scope:
            return False
        return not _REVIEW_SCOPE_OK.search(raw_output)

    lowered = raw_output.lower()
    if any(marker in lowered for marker in _REVIEW_FAILED_MARKERS):
        return True
    if any(marker in lowered for marker in _REVIEW_DEGRADED_MARKERS):
        return True
    if require_scope and not _REVIEW_SCOPE_OK.search(raw_output):
        return True
    return False


def is_clean_review(
    raw_output: str,
    repairable_findings: list[dict[str, str]],
    *,
    git_preflight_ok: bool = False,
    require_scope: bool = True,
) -> bool:
    if repairable_findings:
        return False
    if is_review_failed(
        raw_output,
        git_preflight_ok=git_preflight_ok,
        require_scope=require_scope,
    ):
        return False
    lowered = raw_output.lower()
    if "0 findings" in lowered:
        return True
    if any(pattern.search(raw_output) for pattern in _CLEAN_PATTERNS):
        return True
    # Product lane clean when findings exist but all were excluded (process lane only).
    if extract_findings_table(raw_output):
        return True
    return False


def is_review_inconclusive(
    raw_output: str,
    repairable_findings: list[dict[str, str]],
    *,
    git_preflight_ok: bool = False,
    require_scope: bool = True,
) -> bool:
    if not raw_output.strip():
        return True
    if repairable_findings:
        return False
    if is_review_failed(
        raw_output,
        git_preflight_ok=git_preflight_ok,
        require_scope=require_scope,
    ):
        return False
    return not is_clean_review(
        raw_output,
        repairable_findings,
        git_preflight_ok=git_preflight_ok,
        require_scope=require_scope,
    )


def parse_review_result(
    raw_output: str,
    *,
    git_preflight_ok: bool = False,
    require_scope: bool = True,
) -> ReviewRunResult:
    findings = extract_findings_table(raw_output)
    actionable = [row for row in findings if row["severity"].lower() not in {"info", "informational"}]
    repairable, excluded = partition_repair_findings(actionable)
    fingerprint = build_fingerprint(repairable)
    failed = is_review_failed(
        raw_output,
        git_preflight_ok=git_preflight_ok,
        require_scope=require_scope,
    )
    transient = is_transient_error(raw_output)
    clean = is_clean_review(
        raw_output,
        repairable,
        git_preflight_ok=git_preflight_ok,
        require_scope=require_scope,
    )
    inconclusive = is_review_inconclusive(
        raw_output,
        repairable,
        git_preflight_ok=git_preflight_ok,
        require_scope=require_scope,
    )
    completed = bool(raw_output.strip()) and not transient and not failed and not inconclusive
    return ReviewRunResult(
        completed=completed,
        clean=clean if completed else None,
        findings=findings,
        actionable_findings=actionable,
        repairable_findings=repairable,
        excluded_findings=excluded,
        fingerprint=fingerprint,
        raw_output=raw_output,
        transient_error=transient,
        git_preflight_ok=git_preflight_ok,
    )


def parse_bugbot_result(raw_output: str, *, git_preflight_ok: bool = False) -> ReviewRunResult:
    return parse_review_result(
        raw_output,
        git_preflight_ok=git_preflight_ok,
        require_scope=True,
    )


def parse_security_review_result(raw_output: str, *, git_preflight_ok: bool = False) -> ReviewRunResult:
    """Cursor `/review-security` summary: no REVIEW_SCOPE header required."""
    return parse_review_result(
        raw_output,
        git_preflight_ok=git_preflight_ok,
        require_scope=False,
    )


_HUNT_BLOCKED_STATUSES = frozenset(
    {
        "BLOCKED_NEEDS_INPUT",
        "BLOCKED_PREFLIGHT",
        "BLOCKED_NEEDS_OWNERSHIP",
        "EVIDENCE_GAP",
    }
)

_HUNT_FINDING_BLOCK_RE = re.compile(
    r"###\s*FINDING_ID:\s*(?P<fid>\S+)(?P<body>.*?)(?=\n###\s*FINDING_ID:|\nBUG_TEAM_HANDOFF\b|\nALTERNATE_|\nNEXT_OWNER_ACTION\b|\Z)",
    re.DOTALL | re.IGNORECASE,
)

_HUNT_FIELD_RE = re.compile(
    r"^-\s*(?P<key>[A-Z0-9_]+):\s*(?P<value>.*)$",
    re.MULTILINE,
)

_HUNT_FINDINGS_TOTAL_RE = re.compile(
    r"^-?\s*findings_total:\s*(?P<n>\d+)\s*$",
    re.MULTILINE | re.IGNORECASE,
)


def _hunt_field_map(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in _HUNT_FIELD_RE.finditer(body):
        fields[match.group("key").upper()] = _normalize_cell(match.group("value"))
    return fields


def extract_hunt_findings(raw_output: str) -> list[dict[str, str]]:
    """Map `/opsx-project-bug-hunt` FINDINGS blocks to the 3-column filter shape."""
    findings: list[dict[str, str]] = []
    for match in _HUNT_FINDING_BLOCK_RE.finditer(raw_output):
        fid = _normalize_cell(match.group("fid"))
        fields = _hunt_field_map(match.group("body"))
        location = fields.get("LOC_LINK") or fields.get("PATH") or ""
        title = fields.get("TITLE") or fields.get("LITERAL_CLAIM") or fid
        severity = (
            fields.get("SEVERITY")
            or fields.get("PRODUCTION_CLASSIFICATION")
            or "High"
        )
        if not location:
            continue
        findings.append(
            {
                "severity": severity,
                "location": location,
                "finding": f"{fid}: {title}",
            }
        )
    if findings:
        return findings
    return extract_findings_table(raw_output)


def _parse_findings_total(raw_output: str) -> int | None:
    match = _HUNT_FINDINGS_TOTAL_RE.search(raw_output)
    if not match:
        return None
    return int(match.group("n"))


def parse_project_bug_hunt_result(
    raw_output: str,
    *,
    git_preflight_ok: bool = False,
) -> ReviewRunResult:
    """Parse `/opsx-project-bug-hunt` STATUS + FINDINGS (no REVIEW_SCOPE)."""
    del git_preflight_ok  # hunt uses its own snapshot; preflight flag unused
    if not raw_output.strip():
        return ReviewRunResult(
            completed=False,
            clean=None,
            findings=[],
            actionable_findings=[],
            repairable_findings=[],
            excluded_findings=[],
            fingerprint=None,
            raw_output=raw_output,
            transient_error=False,
        )

    status = (_extract_token(raw_output, "STATUS") or "").upper()
    # Do not treat finding prose that mentions "timeout" as a CLI transient.
    transient = False if status in {"HUNT_COMPLETE", *_HUNT_BLOCKED_STATUSES} else is_transient_error(
        raw_output
    )
    if transient:
        return ReviewRunResult(
            completed=False,
            clean=None,
            findings=[],
            actionable_findings=[],
            repairable_findings=[],
            excluded_findings=[],
            fingerprint=None,
            raw_output=raw_output,
            transient_error=True,
        )
    if status in _HUNT_BLOCKED_STATUSES:
        return ReviewRunResult(
            completed=False,
            clean=None,
            findings=[],
            actionable_findings=[],
            repairable_findings=[],
            excluded_findings=[],
            fingerprint=None,
            raw_output=raw_output,
            transient_error=False,
        )

    findings = extract_hunt_findings(raw_output)
    actionable = [
        row for row in findings if row["severity"].lower() not in {"info", "informational"}
    ]
    repairable, excluded = partition_repair_findings(actionable)
    fingerprint = build_fingerprint(repairable)
    findings_total = _parse_findings_total(raw_output)

    if status and status != "HUNT_COMPLETE" and not findings:
        return ReviewRunResult(
            completed=False,
            clean=None,
            findings=findings,
            actionable_findings=actionable,
            repairable_findings=repairable,
            excluded_findings=excluded,
            fingerprint=fingerprint,
            raw_output=raw_output,
            transient_error=False,
        )

    clean = False
    if not repairable:
        if findings_total == 0:
            clean = True
        elif status == "HUNT_COMPLETE" and not findings:
            clean = True
        elif findings and excluded and not repairable:
            clean = True
        elif status == "HUNT_COMPLETE" and findings_total is None and not findings:
            clean = True

    completed = bool(status == "HUNT_COMPLETE" or findings or clean)
    return ReviewRunResult(
        completed=completed,
        clean=clean if completed else None,
        findings=findings,
        actionable_findings=actionable,
        repairable_findings=repairable,
        excluded_findings=excluded,
        fingerprint=fingerprint,
        raw_output=raw_output,
        transient_error=False,
    )


def _extract_token(raw_output: str, token: str) -> str | None:
    """Take the last token occurrence — orch output often prints STATUS twice (ACTIVE then terminal)."""
    pattern = re.compile(rf"^{re.escape(token)}:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
    matches = pattern.findall(raw_output)
    if not matches:
        return None
    return matches[-1].strip()


_DEFAULT_REPAIR_TERMINALS = {
    "BUG_BATCH_CLEAR",
    "PARTIAL_COMPLETE",
    "BLOCKED_OWNER_DECISION",
    "BLOCKED_WORKER_STATE",
    "BLOCKED_RESOURCE",
    "BLOCKED_GLOBAL_BLOCKER",
    "BLOCKED_WORKTREE_OWNERSHIP",
    "FAIL_CLOSED",
}

_FIX_NOW_NONE_RE = re.compile(
    r"^[\t ]*FIX_NOW:\s*(?:\(none\b|none\b)\s*",
    re.IGNORECASE | re.MULTILINE,
)

_FIX_NOW_BLOCK_RE = re.compile(
    r"FIX_NOW:\s*\n((?:\s+.+\n?)*)",
    re.IGNORECASE,
)


def _infer_repaired_this_batch(raw_output: str) -> bool | None:
    """Infer repair progress when REPAIRED_THIS_BATCH token is absent.

    Orch often prints OWNER_AGGREGATE.FIX_NOW: (none) while still emitting
    BUG_BATCH_CLEAR after defer-only batches — that must count as no repair.
    """
    if _FIX_NOW_NONE_RE.search(raw_output):
        return False
    block = _FIX_NOW_BLOCK_RE.search(raw_output)
    if not block:
        return None
    items: list[str] = []
    for line in block.group(1).splitlines():
        item = line.strip()
        if not item:
            continue
        lowered = item.lower()
        if lowered.startswith("(none") or lowered in {"none", "-"}:
            continue
        if item.startswith("-"):
            items.append(item)
    return bool(items)


def parse_repair_result(
    raw_output: str,
    *,
    extra_terminals: frozenset[str] | set[str] = frozenset(),
    batch_clear_tokens: tuple[str, ...] = ("BUG_BATCH_CLEAR",),
) -> RepairRunResult:
    status = (_extract_token(raw_output, "STATUS") or "").upper()
    mission_status = (_extract_token(raw_output, "MISSION_STATUS") or "").upper()
    running_worker = _extract_token(raw_output, "RUNNING_WORKER")

    unresolved: list[str] = []
    unresolved_block = re.search(
        r"UNRESOLVED:\s*\n((?:\s+.+\n?)*)",
        raw_output,
        re.IGNORECASE,
    )
    if unresolved_block:
        for line in unresolved_block.group(1).splitlines():
            item = line.strip()
            if item and item.lower() not in {"none", "-"}:
                unresolved.append(item)

    terminal_statuses = set(_DEFAULT_REPAIR_TERMINALS)
    terminal_statuses.update(token.upper() for token in extra_terminals)
    completed = status in terminal_statuses or mission_status in terminal_statuses

    batch_clear = False
    for token in batch_clear_tokens:
        token_value = (_extract_token(raw_output, token) or "").upper()
        token_upper = token.upper()
        if token_value == "YES" or status == token_upper or mission_status == token_upper:
            batch_clear = True
            break

    repaired_raw = (_extract_token(raw_output, "REPAIRED_THIS_BATCH") or "").upper()
    repaired_this_batch: bool | None = None
    if repaired_raw in {"YES", "Y", "TRUE"}:
        repaired_this_batch = True
    elif repaired_raw in {"NO", "N", "FALSE"}:
        repaired_this_batch = False
    else:
        repaired_this_batch = _infer_repaired_this_batch(raw_output)

    uncertain = False
    if raw_output.strip() and not completed:
        uncertain = True
    if completed and batch_clear is False and status not in terminal_statuses:
        uncertain = True

    return RepairRunResult(
        completed=completed,
        bug_batch_clear=batch_clear if completed else None,
        unresolved=unresolved,
        running_worker=running_worker,
        raw_output=raw_output,
        uncertain=uncertain,
        repaired_this_batch=repaired_this_batch,
    )


def parse_opsx_bug_fix_result(raw_output: str) -> RepairRunResult:
    return parse_repair_result(raw_output)


def parse_security_repair_result(raw_output: str) -> RepairRunResult:
    return parse_repair_result(
        raw_output,
        extra_terminals={"SECURITY_BATCH_CLEAR"},
        batch_clear_tokens=("SECURITY_BATCH_CLEAR", "BUG_BATCH_CLEAR"),
    )
