"""Read-only PRODUCT security delta report for ``/opsx-security-review``.

Отчёт заново читает change-bound CONFIRMED triage packets и ставит
каждому disposition по текущему файлу. Объявленного ``security_review``
на target нет, поэтому helper не пишет change-local packet и не выдаёт
archive PASS. ``mode=bounded`` не исполняет проверку и не переключается
на отчёт.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import cast


CONTRACT_ID = "OPSX-WB-SECURITY-REVIEW-01"
FORBIDDEN_ARCHIVE_STATUSES = {"PASS", "PASS_WITH_RISK", "NOT_APPLICABLE"}
DISPOSITIONS = {
    "REMEDIATED",
    "STILL_OPEN",
    "NOT_REPRODUCED",
    "DATA_GAP",
    "OUT_OF_SCOPE",
}
WORST_RANK = {
    "STILL_OPEN": 0,
    "DATA_GAP": 1,
    "NOT_REPRODUCED": 2,
    "REMEDIATED": 3,
    "OUT_OF_SCOPE": 4,
}
CHANGE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$")
REVISION_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")
FINDING_CITE_PATTERN = re.compile(r"SEC-FIND-[A-Z0-9][A-Z0-9-]{2,80}")
PACKET_BEGIN = "BEGIN_SECURITY_FINDING_PACKET\n"
PACKET_END = "\nEND_SECURITY_FINDING_PACKET\n"
MAX_EVIDENCE_BYTES = 1024 * 1024
MAX_CITE_FILES = 20
MAX_CITE_BYTES = 200_000
MAX_PACKETS = 500
UNBOUND_STATUSES = {"NEEDS_CHANGE_BINDING", "UNBOUND", "n/a"}
CAPABILITY_CHECK_ID = "security_review"


class SecurityReviewError(Exception):
    """Ожидаемая ошибка контракта запроса, без записи packet."""


def canonical_contained(candidate: Path, approved_root: Path) -> Path | None:
    """Канонический путь, только если он внутри root с учётом separator.

    Совпадение голого строкового префикса соседнего каталога отклоняется.
    Symlink и junction проверяются по resolved target.
    """

    root = approved_root.resolve()
    raw = candidate if candidate.is_absolute() else root / candidate
    try:
        resolved = raw.resolve()
    except OSError:
        return None
    root_text = os.path.normcase(str(root))
    resolved_text = os.path.normcase(str(resolved))
    if resolved_text == root_text:
        return resolved
    prefix = root_text + os.sep
    if resolved_text.startswith(prefix):
        return resolved
    return None


def registry_check_ids(text: str) -> set[str]:
    """Вернуть id проверок из секции ``checks`` static-check registry.

    Разбор линейный и ограничен ключами второго уровня. Это чтение факта
    регистрации, не добавление capability.
    """

    identifiers: set[str] = set()
    in_checks = False
    for raw_line in text.splitlines():
        if raw_line.startswith("checks:"):
            in_checks = True
            continue
        if not in_checks:
            continue
        if raw_line and not raw_line.startswith((" ", "#")):
            break
        if not raw_line.startswith("  ") or raw_line.startswith("   "):
            continue
        name = raw_line.strip()
        if not name.endswith(":") or name.startswith("#"):
            continue
        identifiers.add(name[:-1].strip())
    return identifiers


def review(
    repo_root: Path,
    request: dict[str, object],
    *,
    head_reader: Callable[[Path], str] | None = None,
    registry_text: str | None = None,
) -> dict[str, object]:
    """Собрать delta report или отказать bounded-режиму.

    Args:
        repo_root: единственный approved root.
        request: JSON с ``mode``, для report ещё ``change`` и ``revision``.
        head_reader: источник текущего HEAD. По умолчанию ``git rev-parse``.
        registry_text: текст registry; по умолчанию файл target.

    Returns:
        JSON-отчёт. Archive verdict не выставляется. Файлы не пишутся.
    """

    try:
        return _seal(
            _review(
                repo_root.resolve(),
                request,
                head_reader=head_reader or git_head,
                registry_text=registry_text,
            )
        )
    except SecurityReviewError as error:
        return _seal(_blocked(str(error), mode=_optional_str(request, "mode")))


def git_head(repo_root: Path) -> str:
    """Прочитать текущий HEAD без изменения git state."""

    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        shell=False,
        check=False,
    )
    if completed.returncode != 0:
        raise SecurityReviewError("head_unreadable")
    return completed.stdout.strip()


def build_parser() -> argparse.ArgumentParser:
    """CLI: один request JSON, отчёт в stdout, без записи packet."""

    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--repo-root", type=Path, required=True)
    _ = parser.add_argument("--request-file", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Напечатать disposition JSON. Код 2 только для нечитаемого request."""

    args = build_parser().parse_args(argv)
    try:
        raw = json.loads(args.request_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(json.dumps(_seal(_blocked(f"invalid_request:{error}"))))
        return 2
    if not isinstance(raw, dict):
        print(json.dumps(_seal(_blocked("invalid_request"))))
        return 2
    result = review(args.repo_root, cast(dict[str, object], raw))
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


def _review(
    repo_root: Path,
    request: dict[str, object],
    *,
    head_reader: Callable[[Path], str],
    registry_text: str | None,
) -> dict[str, object]:
    project = _projectization_block(repo_root)
    if project is not None:
        return project
    mode = _optional_str(request, "mode")
    if mode == "bounded":
        return _bounded(repo_root, registry_text)
    if mode != "report":
        return _blocked("unknown_mode" if mode else "mode_required", mode=mode)
    if request.get("index_scan") is False:
        return _blocked("index_scan_required", mode="report")
    change_id = _optional_str(request, "change")
    if not CHANGE_ID_PATTERN.fullmatch(change_id) or ".." in change_id.split("."):
        return _blocked("bad_change_id", mode="report")
    revision = _optional_str(request, "revision")
    if not REVISION_PATTERN.fullmatch(revision):
        return _blocked("bad_revision", mode="report", change=change_id)
    try:
        head = head_reader(repo_root).strip()
    except OSError as error:
        raise SecurityReviewError("head_unreadable") from error
    if not REVISION_PATTERN.fullmatch(head):
        return _blocked("head_unreadable", mode="report", change=change_id)
    if revision.lower() != head.lower():
        return _blocked(
            "revision_not_current",
            mode="report",
            change=change_id,
            revision=revision,
            head=head,
        )
    change_dir = repo_root / "openspec" / "changes" / change_id
    resolved_change = canonical_contained(change_dir, repo_root)
    if resolved_change is None or not resolved_change.is_dir():
        return _blocked(
            "change_not_found",
            mode="report",
            change=change_id,
            revision=revision,
            head=head,
        )
    discovered = _discover_index(repo_root, change_id)
    if discovered["status"] == "BLOCKED":
        return _blocked(
            str(discovered["reason"]),
            mode="report",
            change=change_id,
            revision=revision,
            head=head,
        )
    packets = cast(list[dict[str, object]], discovered["packets"])
    index_problem = str(discovered["index_problem"])
    groups = _group_packets(packets)
    findings: list[dict[str, object]] = []
    duplicates: list[dict[str, object]] = []
    for group in groups:
        reduced = _reduce_group(
            repo_root,
            group,
            revision=revision,
            remediation=_remediation_map(request),
        )
        if reduced["status"] == "BLOCKED":
            return _blocked(
                str(reduced["reason"]),
                mode="report",
                change=change_id,
                revision=revision,
                head=head,
            )
        findings.append(cast(dict[str, object], reduced["finding"]))
        duplicates.extend(cast(list[dict[str, object]], reduced["duplicates"]))
    cite_problem, cited_ids = _cited_ids(repo_root, resolved_change)
    if cite_problem and not index_problem:
        index_problem = cite_problem
    known_ids = {str(row["SECURITY_FINDING_ID"]) for row in findings}
    known_ids.update(str(row["SECURITY_FINDING_ID"]) for row in duplicates)
    for finding_id in sorted(cited_ids | set(_id_list(request, "security_findings"))):
        if finding_id in known_ids:
            continue
        findings.append(
            _gap_row(
                finding_id=finding_id,
                reason="finding_not_in_index",
                plane="unknown",
            )
        )
        known_ids.add(finding_id)
    index_hits = [
        str(packet["SECURITY_FINDING_ID"])
        for packet in packets
        if isinstance(packet.get("SECURITY_FINDING_ID"), str)
    ]
    accounted = {str(row["SECURITY_FINDING_ID"]) for row in findings}
    accounted.update(str(row["SECURITY_FINDING_ID"]) for row in duplicates)
    if not set(index_hits) <= accounted:
        return _blocked(
            "skipped_finding",
            mode="report",
            change=change_id,
            revision=revision,
            head=head,
        )
    status, reason = _overall(findings, index_problem)
    declared = _capability_declared(repo_root)
    return _report(
        status=status,
        reason=reason,
        change=change_id,
        revision=revision,
        head=head,
        findings=findings,
        duplicates=duplicates,
        index_hits=index_hits,
        index_problem=index_problem,
        security_review_declared="YES" if declared else "NO",
    )


def _bounded(repo_root: Path, registry_text: str | None) -> dict[str, object]:
    if registry_text is None:
        registry_path = repo_root / ".cursor" / "static-check" / "registry.yaml"
        resolved = canonical_contained(registry_path, repo_root)
        if resolved is None or not resolved.is_file():
            detail = "S6_SECURITY_REVIEW_CAPABILITY_UNREGISTERED"
            return _bounded_body(detail)
        registry_text = resolved.read_text(encoding="utf-8")
    identifiers = registry_check_ids(registry_text)
    if CAPABILITY_CHECK_ID not in identifiers:
        detail = "S6_SECURITY_REVIEW_CAPABILITY_UNREGISTERED"
    else:
        detail = "S6_SECURITY_REVIEW_RESULT_WRITER_NOT_INSTALLED"
    return _bounded_body(detail)


def _bounded_body(detail: str) -> dict[str, object]:
    body = _blocked("BINDING_GAP", mode="bounded")
    body["DETAIL"] = detail
    body["INDEX_SCAN"] = "not_run"
    body["FALLBACK"] = "none"
    return body


def _discover_index(repo_root: Path, change_id: str) -> dict[str, object]:
    store = repo_root / "opsx" / "runs" / "security-cases"
    resolved_store = canonical_contained(store, repo_root)
    if resolved_store is None:
        return {"status": "BLOCKED", "reason": "path_outside_workspace", "packets": []}
    if not resolved_store.exists():
        return {
            "status": "OK",
            "reason": "",
            "packets": [],
            "index_problem": "",
        }
    if not resolved_store.is_dir():
        return {
            "status": "OK",
            "reason": "",
            "packets": [],
            "index_problem": "index_unreadable",
        }
    packets: list[dict[str, object]] = []
    index_problem = ""
    seen_files = 0
    try:
        case_dirs = sorted(resolved_store.iterdir(), key=lambda item: item.name)
    except OSError:
        return {
            "status": "OK",
            "reason": "",
            "packets": [],
            "index_problem": "index_unreadable",
        }
    for case_dir in case_dirs:
        contained_case = canonical_contained(case_dir, resolved_store)
        if contained_case is None or not contained_case.is_dir():
            index_problem = index_problem or "index_unreadable"
            continue
        findings_dir = contained_case / "findings"
        if not findings_dir.is_dir():
            continue
        try:
            files = sorted(findings_dir.glob("*.md"), key=lambda item: item.name)
        except OSError:
            index_problem = index_problem or "index_unreadable"
            continue
        for packet_file in files:
            seen_files += 1
            if seen_files > MAX_PACKETS:
                index_problem = index_problem or "index_scan_budget"
                return {
                    "status": "OK",
                    "reason": "",
                    "packets": packets,
                    "index_problem": index_problem,
                }
            contained = canonical_contained(packet_file, resolved_store)
            if contained is None or not contained.is_file():
                index_problem = index_problem or "index_unreadable"
                continue
            parsed = _parse_packet(contained)
            if parsed is None:
                index_problem = index_problem or "index_unreadable"
                continue
            selected = _select_packet(parsed, change_id, contained, repo_root)
            if selected is None:
                continue
            if selected.get("_problem"):
                index_problem = index_problem or str(selected["_problem"])
                continue
            packets.append(selected)
    return {
        "status": "OK",
        "reason": "",
        "packets": packets,
        "index_problem": index_problem,
    }


def _select_packet(
    packet: dict[str, object],
    change_id: str,
    packet_file: Path,
    repo_root: Path,
) -> dict[str, object] | None:
    if str(packet.get("TRIAGE_STATUS", "")) != "CONFIRMED":
        return None
    binding = packet.get("CHANGE_BINDING")
    if not isinstance(binding, dict):
        return {"_problem": "ambiguous_change_binding"}
    typed = cast(dict[str, object], binding)
    bound_change = str(typed.get("CHANGE_ID", ""))
    if bound_change != change_id:
        return None
    status = str(typed.get("STATUS", ""))
    if status in UNBOUND_STATUSES:
        return None
    if status != "BOUND":
        return {"_problem": "ambiguous_change_binding"}
    finding_id = packet.get("SECURITY_FINDING_ID")
    if not isinstance(finding_id, str) or not finding_id:
        return {"_problem": "index_unreadable"}
    selected = dict(packet)
    try:
        relative = packet_file.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return {"_problem": "index_unreadable"}
    selected["_EVIDENCE_PATH"] = relative
    return selected


def _group_packets(
    packets: list[dict[str, object]],
) -> list[list[dict[str, object]]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    order: list[str] = []
    for packet in packets:
        signal_hash = str(packet.get("SOURCE_SIGNAL_HASH", ""))
        finding_id = str(packet.get("SECURITY_FINDING_ID", ""))
        key = signal_hash if signal_hash else f"missing-hash:{finding_id}"
        if key not in grouped:
            order.append(key)
            grouped[key] = []
        grouped[key].append(packet)
    return [grouped[key] for key in order]


def _reduce_group(
    repo_root: Path,
    group: list[dict[str, object]],
    *,
    revision: str,
    remediation: dict[str, list[str]],
) -> dict[str, object]:
    evaluated: list[dict[str, object]] = []
    for packet in group:
        one = _evaluate_packet(repo_root, packet, revision=revision, remediation=remediation)
        if one.get("_blocked"):
            return {"status": "BLOCKED", "reason": one["_blocked"]}
        evaluated.append(one)
    evaluated.sort(key=lambda row: str(row["EVIDENCE_PATH"]))
    planes = {
        str(row["AFFECTED_PLANE"])
        for row in evaluated
        if str(row["AFFECTED_PLANE"]) in {"PRODUCT", "CONTROL_PLANE", "INFRASTRUCTURE"}
    }
    if len(planes) > 1:
        winner = dict(evaluated[0])
        winner["FINDING_DISPOSITION"] = "DATA_GAP"
        winner["DISPOSITION_REASON"] = "plane_conflict"
        winner["INDEPENDENT_EVIDENCE"] = []
        winner["CAUSAL_EVIDENCE_SUFFICIENT"] = "no"
        winner["SECURITY_LEARNING_ELIGIBILITY"] = "NOT_ELIGIBLE"
        winner["PRODUCT_VERDICT"] = "not_issued"
    else:
        winner = min(
            evaluated,
            key=lambda row: WORST_RANK[str(row["FINDING_DISPOSITION"])],
        )
    canonical = dict(winner)
    duplicates: list[dict[str, object]] = []
    canonical_id = str(canonical["SECURITY_FINDING_ID"])
    for row in evaluated:
        finding_id = str(row["SECURITY_FINDING_ID"])
        if finding_id == canonical_id and row["EVIDENCE_PATH"] == canonical["EVIDENCE_PATH"]:
            continue
        duplicates.append(
            {
                "SECURITY_FINDING_ID": finding_id,
                "SECURITY_CASE_ID": row["SECURITY_CASE_ID"],
                "SOURCE_SIGNAL_HASH": row["SOURCE_SIGNAL_HASH"],
                "CANONICAL_FINDING_ID": canonical_id,
                "EVIDENCE_PATH": row["EVIDENCE_PATH"],
                "REASON": "duplicate_signal_hash",
            }
        )
    return {"status": "OK", "finding": canonical, "duplicates": duplicates}


def _evaluate_packet(
    repo_root: Path,
    packet: dict[str, object],
    *,
    revision: str,
    remediation: dict[str, list[str]],
) -> dict[str, object]:
    finding_id = str(packet.get("SECURITY_FINDING_ID", ""))
    case_id = str(packet.get("SECURITY_CASE_ID", "none"))
    signal_hash = str(packet.get("SOURCE_SIGNAL_HASH", ""))
    plane = str(packet.get("AFFECTED_PLANE", "unknown"))
    evidence_path = str(packet.get("_EVIDENCE_PATH", ""))
    base = {
        "SECURITY_CASE_ID": case_id,
        "SECURITY_FINDING_ID": finding_id,
        "EVIDENCE_PATH": evidence_path,
        "SOURCE_SIGNAL_HASH": signal_hash,
        "AFFECTED_PLANE": plane,
        "INDEPENDENT_EVIDENCE": [],
        "CAUSAL_EVIDENCE_SUFFICIENT": "no",
        "SECURITY_LEARNING_ELIGIBILITY": "NOT_ELIGIBLE",
        "PRODUCT_VERDICT": "not_issued",
    }
    if not signal_hash:
        return _with_disposition(base, "DATA_GAP", "missing_signal_hash")
    if plane in {"CONTROL_PLANE", "INFRASTRUCTURE"}:
        row = _with_disposition(base, "OUT_OF_SCOPE", "wrong_plane")
        row["PRODUCT_VERDICT"] = "not_set"
        return row
    if plane != "PRODUCT":
        return _with_disposition(base, "DATA_GAP", "plane_unclassified")
    observed = _observed_revision(packet)
    if observed and observed.lower() != revision.lower():
        return _with_disposition(base, "DATA_GAP", "stale_revision")
    relative = str(packet.get("PATH", ""))
    if _secret_refused(relative):
        return _with_disposition(base, "DATA_GAP", "secret_path_refused")
    contained = canonical_contained(Path(relative), repo_root)
    if contained is None:
        return {"_blocked": "path_outside_workspace"}
    quotes = _packet_quotes(packet)
    if not quotes:
        return _with_disposition(base, "DATA_GAP", "missing_evidence_quotes")
    if not contained.is_file():
        return _with_disposition(base, "DATA_GAP", "evidence_absent")
    try:
        size = contained.stat().st_size
    except OSError:
        return _with_disposition(base, "DATA_GAP", "evidence_absent")
    if size > MAX_EVIDENCE_BYTES:
        return _with_disposition(base, "DATA_GAP", "evidence_read_budget")
    try:
        text = contained.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return _with_disposition(base, "DATA_GAP", "evidence_unreadable")
    present = [quote for quote in quotes if quote in text]
    if present:
        return _with_disposition(
            base,
            "STILL_OPEN",
            "original_quote_present",
            evidence=_cites(relative, text, present),
            sufficient="yes",
        )
    fix_quotes = [
        quote
        for quote in remediation.get(finding_id, [])
        if quote not in quotes
    ]
    fix_hits = [quote for quote in fix_quotes if quote in text]
    if fix_quotes and len(fix_hits) == len(fix_quotes):
        return _with_disposition(
            base,
            "REMEDIATED",
            "independent_fix_quote_present",
            evidence=_cites(relative, text, fix_hits),
            sufficient="yes",
            eligible="ELIGIBLE",
        )
    if fix_hits:
        return _with_disposition(base, "DATA_GAP", "partial_remediation_evidence")
    return _with_disposition(base, "NOT_REPRODUCED", "original_quote_absent")


def _cited_ids(repo_root: Path, change_dir: Path) -> tuple[str, set[str]]:
    try:
        files = sorted(
            path
            for path in change_dir.rglob("*.md")
            if path.is_file() and canonical_contained(path, repo_root) is not None
        )
    except OSError:
        return "cite_scan_unreadable", set()
    if len(files) > MAX_CITE_FILES:
        return "cite_scan_budget", set()
    found: set[str] = set()
    for path in files:
        try:
            if path.stat().st_size > MAX_CITE_BYTES:
                return "cite_scan_budget", found
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return "cite_scan_unreadable", found
        found.update(FINDING_CITE_PATTERN.findall(text))
    return "", found


def _overall(
    findings: list[dict[str, object]], index_problem: str
) -> tuple[str, str]:
    product_open = [
        row
        for row in findings
        if row.get("FINDING_DISPOSITION") == "STILL_OPEN"
        and row.get("AFFECTED_PLANE") == "PRODUCT"
    ]
    if product_open:
        return "FAIL", "product_finding_still_open"
    if index_problem:
        return "DATA_GAP", index_problem
    if any(row.get("DISPOSITION_REASON") == "stale_revision" for row in findings):
        return "DATA_GAP", "stale_revision"
    if any(row.get("FINDING_DISPOSITION") == "DATA_GAP" for row in findings):
        return "DATA_GAP", "evidence_incomplete"
    if any(row.get("FINDING_DISPOSITION") == "NOT_REPRODUCED" for row in findings):
        return "DATA_GAP", "not_reproduced"
    product = [row for row in findings if row.get("AFFECTED_PLANE") == "PRODUCT"]
    if findings and not product:
        return "DATA_GAP", "wrong_plane_no_product_verdict"
    return "DATA_GAP", "undeclared_surface_coverage"


def _report(
    *,
    status: str,
    reason: str,
    change: str,
    revision: str,
    head: str,
    findings: list[dict[str, object]],
    duplicates: list[dict[str, object]],
    index_hits: list[str],
    index_problem: str,
    security_review_declared: str,
) -> dict[str, object]:
    open_ids = [
        str(row["SECURITY_FINDING_ID"])
        for row in findings
        if row.get("FINDING_DISPOSITION") == "STILL_OPEN"
        and row.get("AFFECTED_PLANE") == "PRODUCT"
    ]
    return {
        "STATUS": status,
        "REASON": reason,
        "MODE": "report",
        "CONTRACT_ID": CONTRACT_ID,
        "CHANGE": change,
        "REVISION": revision.lower(),
        "HEAD": head.lower(),
        "IDENTITY_MATCH": "yes",
        "INDEX_SCAN": "unreadable" if index_problem == "index_unreadable" else "complete",
        "INDEX_HITS": index_hits,
        "FINDINGS": findings,
        "DUPLICATE_FINDINGS": duplicates,
        "FINDINGS_OPEN": open_ids,
        "SURFACE_COVERAGE": "undeclared",
        "SECURITY_REVIEW_DECLARED": security_review_declared,
        "FALLBACK": "none",
    }


def _blocked(
    reason: str,
    *,
    mode: str = "",
    change: str = "",
    revision: str = "",
    head: str = "",
) -> dict[str, object]:
    return {
        "STATUS": "BLOCKED",
        "REASON": reason,
        "MODE": mode or "rejected",
        "CONTRACT_ID": CONTRACT_ID,
        "CHANGE": change,
        "REVISION": revision.lower(),
        "HEAD": head.lower(),
        "IDENTITY_MATCH": "yes" if revision and head and revision.lower() == head.lower() else "no",
        "INDEX_SCAN": "not_run",
        "INDEX_HITS": [],
        "FINDINGS": [],
        "DUPLICATE_FINDINGS": [],
        "FINDINGS_OPEN": [],
        "SURFACE_COVERAGE": "undeclared",
        "SECURITY_REVIEW_DECLARED": "NO",
        "FALLBACK": "none",
    }


def _seal(body: dict[str, object]) -> dict[str, object]:
    status = str(body.get("STATUS", ""))
    if status in FORBIDDEN_ARCHIVE_STATUSES or status not in {"BLOCKED", "DATA_GAP", "FAIL"}:
        body["STATUS"] = "BLOCKED"
        body["REASON"] = "false_pass_prevented"
    body["ARCHIVE_VERDICT"] = "not_issued"
    body["PRODUCT_SECURITY_VERDICT"] = "not_issued"
    body["DURABLE_RECEIPT"] = "NONE"
    body["SECURITY_REVIEW_PACKET"] = "absent"
    body["SOURCE_PARITY"] = "PARTIAL"
    body["CAPABILITY_PARITY"] = "PARTIAL"
    body["VERIFY_CONSUMES_SECURITY_REVIEW"] = "NO"
    body["FORBIDDEN_ARCHIVE_STATUS_EMITTED"] = "NO"
    body["GREEN_WRITER"] = "NO"
    body["ARCHIVE_VERDICT_WRITER"] = "NO"
    body["COMMIT_ALLOWED"] = "NO"
    return body


def _with_disposition(
    base: dict[str, object],
    disposition: str,
    reason: str,
    *,
    evidence: list[dict[str, object]] | None = None,
    sufficient: str = "no",
    eligible: str = "NOT_ELIGIBLE",
) -> dict[str, object]:
    if disposition not in DISPOSITIONS:
        raise SecurityReviewError("bad_disposition")
    row = dict(base)
    row["FINDING_DISPOSITION"] = disposition
    row["DISPOSITION_REASON"] = reason
    row["INDEPENDENT_EVIDENCE"] = evidence or []
    row["CAUSAL_EVIDENCE_SUFFICIENT"] = sufficient
    row["SECURITY_LEARNING_ELIGIBILITY"] = eligible
    return row


def _gap_row(*, finding_id: str, reason: str, plane: str) -> dict[str, object]:
    return _with_disposition(
        {
            "SECURITY_CASE_ID": "none",
            "SECURITY_FINDING_ID": finding_id,
            "EVIDENCE_PATH": "none",
            "SOURCE_SIGNAL_HASH": "",
            "AFFECTED_PLANE": plane,
            "PRODUCT_VERDICT": "not_issued",
        },
        "DATA_GAP",
        reason,
    )


def _cites(relative: str, text: str, quotes: list[str]) -> list[dict[str, object]]:
    cites: list[dict[str, object]] = []
    for quote in quotes:
        cites.append(
            {"PATH": relative, "QUOTE": quote, "LINE": _line_of(text, quote)}
        )
    return cites


def _line_of(text: str, quote: str) -> int:
    for index, line in enumerate(text.split("\n"), start=1):
        if quote in line:
            return index
    return 0


def _packet_quotes(packet: dict[str, object]) -> list[str]:
    evidence = packet.get("EVIDENCE")
    if not isinstance(evidence, list):
        return []
    quotes: list[str] = []
    for item in cast(list[object], evidence):
        if not isinstance(item, dict):
            continue
        quote = cast(dict[str, object], item).get("QUOTE")
        if isinstance(quote, str) and quote:
            quotes.append(quote)
    return quotes


def _observed_revision(packet: dict[str, object]) -> str:
    direct = packet.get("OBSERVED_REVISION")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    binding = packet.get("CHANGE_BINDING")
    if isinstance(binding, dict):
        value = cast(dict[str, object], binding).get("REVISION")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _secret_refused(relative: str) -> bool:
    for part in relative.replace("\\", "/").split("/"):
        lower = part.lower()
        if lower.startswith(".env") or lower in {"id_rsa", "credentials.json"}:
            return True
        if "secret" in lower or lower.endswith(".pem") or lower.endswith(".key"):
            return True
    return False


def _parse_packet(path: Path) -> dict[str, object] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    start = text.find(PACKET_BEGIN)
    end = text.find(PACKET_END)
    if start < 0 or end < 0 or end <= start:
        return None
    try:
        value = json.loads(text[start + len(PACKET_BEGIN) : end])
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    return cast(dict[str, object], value)


def _projectization_block(repo_root: Path) -> dict[str, object] | None:
    from opsx_runtime_gates import projectization_and_adoption_block

    return projectization_and_adoption_block(
        repo_root,
        "python.pipeline.security_review",
        blocked_factory=_blocked,
    )


def _capability_declared(repo_root: Path) -> bool:
    path = repo_root / "opsx" / "adapters" / "poetry-python" / "README.md"
    resolved = canonical_contained(path, repo_root)
    if resolved is None or not resolved.is_file():
        return False
    for raw_line in resolved.read_text(encoding="utf-8").splitlines():
        if raw_line.strip() == "security_review: AVAILABLE":
            return True
    return False


def _yaml_scalar(text: str, key: str) -> str:
    prefix = f"{key}:"
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith(prefix):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    return ""


def _optional_str(request: dict[str, object], key: str) -> str:
    value = request.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SecurityReviewError(f"bad_field:{key}")
    return value.strip()


def _id_list(request: dict[str, object], key: str) -> list[str]:
    value = request.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise SecurityReviewError(f"bad_field:{key}")
    identifiers: list[str] = []
    for item in cast(list[object], value):
        if not isinstance(item, str) or not item.strip():
            raise SecurityReviewError(f"bad_field:{key}")
        identifiers.append(item.strip())
    return identifiers


def _remediation_map(request: dict[str, object]) -> dict[str, list[str]]:
    value = request.get("remediation_evidence", {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SecurityReviewError("bad_field:remediation_evidence")
    mapped: dict[str, list[str]] = {}
    for key, quotes in cast(dict[str, object], value).items():
        if not isinstance(quotes, list):
            raise SecurityReviewError("bad_field:remediation_evidence")
        cleaned: list[str] = []
        for quote in cast(list[object], quotes):
            if not isinstance(quote, str) or not quote:
                raise SecurityReviewError("bad_field:remediation_evidence")
            cleaned.append(quote)
        mapped[key] = cleaned
    return mapped


if __name__ == "__main__":
    raise SystemExit(main())
