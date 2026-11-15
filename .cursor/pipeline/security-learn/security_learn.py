"""Class-learning from reviewed security evidence only.

Never learns from raw unreviewed findings. PRODUCT evidence must be a
fresh identity-bound ``/opsx-security-review`` report with REMEDIATED.
CONTROL_PLANE evidence must be a durable control-plane review packet.
INFRASTRUCTURE remains an authority gap.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Callable, cast


CONTRACT_ID = "OPSX-WB-SECURITY-LEARN-01"
PACKET_BEGIN = "BEGIN_SECURITY_FINDING_PACKET\n"
PACKET_END = "\nEND_SECURITY_FINDING_PACKET\n"
REVIEW_BEGIN = "BEGIN_CONTROL_PLANE_SECURITY_REVIEW_PACKET\n"
REVIEW_END = "\nEND_CONTROL_PLANE_SECURITY_REVIEW_PACKET\n"
LESSON_BEGIN = "BEGIN_SECURITY_LESSON_PACKET\n"
LESSON_END = "\nEND_SECURITY_LESSON_PACKET\n"
CASE_ID_PATTERN = re.compile(r"^SEC-CASE-[A-Z0-9][A-Z0-9-]{2,80}$")
FINDING_ID_PATTERN = re.compile(r"^SEC-FIND-[A-Z0-9][A-Z0-9-]{2,80}$")
REVISION_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")
INCIDENT_MARKERS = (
    "openspec/changes/",
    ".cursor/commands/",
    "app/",
    "tests/",
    "SEC-CASE-",
    "SEC-FIND-",
)


class SecurityLearnError(Exception):
    """Learning refusal that must not mint a lesson."""


def canonical_contained(candidate: Path, approved_root: Path) -> Path | None:
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


def learn(
    repo_root: Path,
    request: dict[str, object],
    *,
    head_reader: Callable[[Path], str] | None = None,
) -> dict[str, object]:
    try:
        return _seal(
            _learn(
                repo_root.resolve(),
                request,
                head_reader=head_reader or git_head,
            )
        )
    except SecurityLearnError as error:
        return _seal(_blocked(str(error)))


def git_head(repo_root: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        shell=False,
        check=False,
    )
    if completed.returncode != 0:
        raise SecurityLearnError("head_unreadable")
    return completed.stdout.strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--repo-root", type=Path, required=True)
    _ = parser.add_argument("--request-file", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        raw = json.loads(args.request_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(json.dumps(_seal(_blocked(f"invalid_request:{error}"))))
        return 2
    if not isinstance(raw, dict):
        print(json.dumps(_seal(_blocked("invalid_request"))))
        return 2
    result = learn(args.repo_root, cast(dict[str, object], raw))
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


def _learn(
    repo_root: Path,
    request: dict[str, object],
    *,
    head_reader: Callable[[Path], str],
) -> dict[str, object]:
    blocked = _projectization_block(repo_root)
    if blocked is not None:
        return blocked
    case_id = _opt(request, "case")
    finding_id = _opt(request, "finding")
    if not CASE_ID_PATTERN.fullmatch(case_id) or not FINDING_ID_PATTERN.fullmatch(
        finding_id
    ):
        return _blocked("missing_identity")
    revision = _opt(request, "revision")
    head = head_reader(repo_root).strip()
    if not REVISION_PATTERN.fullmatch(head):
        return _blocked("head_unreadable")
    if revision:
        if not REVISION_PATTERN.fullmatch(revision):
            return _blocked("bad_revision")
        if revision.lower() != head.lower():
            return _blocked("stale_revision")
    else:
        revision = head
    finding_path = _opt(request, "finding_path") or str(
        Path("opsx")
        / "runs"
        / "security-cases"
        / case_id
        / "findings"
        / f"{finding_id}.md"
    )
    opened = _open_text(repo_root, finding_path)
    if isinstance(opened, dict):
        return opened
    packet = _parse_json_block(opened, PACKET_BEGIN, PACKET_END)
    if packet is None:
        return _blocked("finding_packet_unreadable")
    if str(packet.get("TRIAGE_STATUS")) != "CONFIRMED":
        return _blocked("finding_not_confirmed")
    if str(packet.get("SECURITY_FINDING_ID")) != finding_id:
        return _blocked("finding_mismatch")
    plane = str(packet.get("AFFECTED_PLANE") or "")
    if plane == "INFRASTRUCTURE":
        return _blocked("BLOCKED_AUTHORITY_GAP")
    review_payload = _load_review(repo_root, request, plane=plane, finding_id=finding_id)
    if review_payload.get("STATUS") == "BLOCKED":
        return review_payload
    if str(review_payload.get("REVISION") or "").lower() != revision.lower():
        return _blocked("stale_review_evidence")
    if str(review_payload.get("SECURITY_FINDING_ID")) != finding_id:
        return _blocked("review_finding_mismatch")
    if str(review_payload.get("FINDING_DISPOSITION")) != "REMEDIATED":
        return _blocked("unreviewed_or_unremediated")
    signal_hash = str(packet.get("SOURCE_SIGNAL_HASH") or "")
    review_hash = str(review_payload.get("SOURCE_SIGNAL_HASH") or signal_hash)
    if signal_hash and review_hash and signal_hash != review_hash:
        return _blocked("identity_mismatch")
    lesson_class = _opt(request, "lesson_class")
    lesson_text = _opt(request, "lesson_text")
    if not lesson_class or not lesson_text:
        return _blocked("lesson_body_required")
    if _incident_specific(lesson_text):
        return {
            "STATUS": "NO_LESSON",
            "LESSON_STATUS": "NO_LESSON",
            "REASON": "INCIDENT_SPECIFIC_NOT_TRANSFERABLE",
            "PASS": "NO",
        }
    payload = {
        "schema_version": "target-security-lesson-v1",
        "CONTRACT_ID": CONTRACT_ID,
        "SECURITY_CASE_ID": case_id,
        "SECURITY_FINDING_ID": finding_id,
        "AFFECTED_PLANE": plane,
        "SOURCE_SIGNAL_HASH": signal_hash,
        "REVISION": revision,
        "LESSON_CLASS": lesson_class,
        "LESSON_TEXT": lesson_text,
        "LESSON_STATUS": "RECORDED",
        "REVIEW_DISPOSITION": "REMEDIATED",
    }
    relative = (
        Path("opsx") / "runs" / "security-learning" / case_id / f"{finding_id}.md"
    )
    target = canonical_contained(repo_root / relative, repo_root)
    if target is None:
        return _blocked("path_outside_workspace")
    target.parent.mkdir(parents=True, exist_ok=True)
    body = (
        "# SECURITY_LESSON_PACKET\n\n"
        f"{LESSON_BEGIN}"
        f"{json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True)}\n"
        f"{LESSON_END}"
    )
    tmp = target.with_suffix(".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, target)
    return {
        "STATUS": "LESSON_RECORDED",
        "LESSON_STATUS": "RECORDED",
        "PASS": "NO",
        "ARCHIVE_VERDICT": "not_issued",
        "LESSON_PATH": relative.as_posix(),
        "REVISION": revision,
        "SECURITY_LEARNING_AUTHORITY": "YES",
        "SECURITY_REVIEW_VERDICT_AUTHORITY": "NO",
    }


def _load_review(
    repo_root: Path,
    request: dict[str, object],
    *,
    plane: str,
    finding_id: str,
) -> dict[str, object]:
    review_path = _opt(request, "review_path")
    if plane == "CONTROL_PLANE":
        if not review_path:
            case_id = _opt(request, "case")
            review_path = str(
                Path("opsx")
                / "runs"
                / "security-cases"
                / case_id
                / "reviews"
                / "control-plane"
                / f"{finding_id}.md"
            )
        opened = _open_text(repo_root, review_path)
        if isinstance(opened, dict):
            return opened
        parsed = _parse_json_block(opened, REVIEW_BEGIN, REVIEW_END)
        if parsed is None:
            return _blocked("review_packet_unreadable")
        parsed.setdefault("STATUS", parsed.get("VERDICT"))
        return parsed
    if plane == "PRODUCT":
        if not review_path:
            return _blocked("product_review_path_required")
        opened = _open_text(repo_root, review_path)
        if isinstance(opened, dict):
            return opened
        try:
            parsed = json.loads(opened)
        except json.JSONDecodeError:
            return _blocked("product_review_unreadable")
        if not isinstance(parsed, dict):
            return _blocked("product_review_unreadable")
        if parsed.get("ARCHIVE_VERDICT") not in {None, "not_issued", "NONE"}:
            if parsed.get("ARCHIVE_VERDICT") in {"PASS", "PASS_WITH_RISK"}:
                return _blocked("archive_verdict_is_not_learning_evidence")
        findings = parsed.get("findings")
        matched: dict[str, object] | None = None
        if isinstance(findings, list):
            for row in findings:
                if isinstance(row, dict) and str(row.get("SECURITY_FINDING_ID")) == finding_id:
                    matched = row
                    break
        disposition = ""
        if matched is not None:
            disposition = str(matched.get("FINDING_DISPOSITION") or "")
        elif str(parsed.get("SECURITY_FINDING_ID")) == finding_id:
            disposition = str(parsed.get("FINDING_DISPOSITION") or "")
        if not disposition:
            return _blocked("product_finding_not_in_review")
        return {
            "STATUS": str(parsed.get("STATUS") or "REPORT"),
            "REVISION": str(parsed.get("revision") or parsed.get("REVISION") or ""),
            "SECURITY_FINDING_ID": finding_id,
            "FINDING_DISPOSITION": disposition,
            "SOURCE_SIGNAL_HASH": str(parsed.get("SOURCE_SIGNAL_HASH") or ""),
        }
    return _blocked("plane_unknown")


def _incident_specific(text: str) -> bool:
    lowered = text.lower()
    if any(marker.lower() in text for marker in INCIDENT_MARKERS):
        return True
    if re.search(r"\bdef [a-zA-Z_][a-zA-Z0-9_]*\b", text):
        return True
    if re.search(r"[A-Za-z]:\\", text) or "http://" in lowered or "https://" in lowered:
        return True
    return False


def _parse_json_block(text: str, begin: str, end: str) -> dict[str, object] | None:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    start = text.find(begin)
    stop = text.find(end)
    if start < 0 or stop < 0 or stop <= start:
        return None
    blob = text[start + len(begin) : stop]
    try:
        value = json.loads(blob)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _open_text(repo_root: Path, relative: str) -> str | dict[str, object]:
    resolved = canonical_contained(Path(relative), repo_root)
    if resolved is None or not resolved.is_file():
        return _blocked("path_outside_workspace")
    data = resolved.read_bytes()
    if len(data) > 1_000_000:
        return _blocked("file_too_large")
    return data.decode("utf-8")


def _projectization_block(repo_root: Path) -> dict[str, object] | None:
    from opsx_runtime_gates import projectization_and_adoption_block

    return projectization_and_adoption_block(
        repo_root,
        "python.pipeline.security_learn",
        blocked_factory=_blocked,
    )


def _opt(request: dict[str, object], key: str) -> str:
    value = request.get(key)
    return value.strip() if isinstance(value, str) else ""


def _blocked(reason: str, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "STATUS": "BLOCKED",
        "LESSON_STATUS": "BLOCKED",
        "REASON": reason,
        "PASS": "NO",
        "SECURITY_LEARNING_AUTHORITY": "YES",
        "SECURITY_REVIEW_VERDICT_AUTHORITY": "NO",
        "GREEN_WRITER": "NO",
        "ARCHIVE_VERDICT_WRITER": "NO",
    }
    payload.update(extra)
    return payload


def _seal(result: dict[str, object]) -> dict[str, object]:
    sealed = dict(result)
    sealed.setdefault("CONTRACT_ID", CONTRACT_ID)
    sealed.setdefault("SOURCE_PARITY", "ADAPTED")
    sealed.setdefault("COMMIT_ALLOWED", "NO")
    sealed.setdefault("ARCHIVE_VERDICT", "not_issued")
    return sealed


if __name__ == "__main__":
    raise SystemExit(main())
