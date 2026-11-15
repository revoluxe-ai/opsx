"""Independent CONTROL_PLANE security verifier.

Writes only under ``opsx/runs/security-cases/<case>/reviews/control-plane/``.
Does not remediate, does not issue product or archive verdicts, and fails
closed on stale, wrong-plane, or missing evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Callable, cast


CONTRACT_ID = "OPSX-WB-CONTROL-PLANE-SECURITY-REVIEW-01"
PACKET_BEGIN = "BEGIN_SECURITY_FINDING_PACKET\n"
PACKET_END = "\nEND_SECURITY_FINDING_PACKET\n"
REVIEW_BEGIN = "BEGIN_CONTROL_PLANE_SECURITY_REVIEW_PACKET\n"
REVIEW_END = "\nEND_CONTROL_PLANE_SECURITY_REVIEW_PACKET\n"
CASE_ID_PATTERN = re.compile(r"^SEC-CASE-[A-Z0-9][A-Z0-9-]{2,80}$")
FINDING_ID_PATTERN = re.compile(r"^SEC-FIND-[A-Z0-9][A-Z0-9-]{2,80}$")
REVISION_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")
VERDICTS = {"PASS", "FAIL", "BLOCKED", "NOT_APPLICABLE"}


class ControlPlaneReviewError(Exception):
    """Verifier refusal that is not a product PASS."""


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


def review(
    repo_root: Path,
    request: dict[str, object],
    *,
    head_reader: Callable[[Path], str] | None = None,
) -> dict[str, object]:
    try:
        return _seal(
            _review(
                repo_root.resolve(),
                request,
                head_reader=head_reader or git_head,
            )
        )
    except ControlPlaneReviewError as error:
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
        raise ControlPlaneReviewError("head_unreadable")
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
    result = review(args.repo_root, cast(dict[str, object], raw))
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


def _review(
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
    packet = _parse_finding(opened)
    if packet is None:
        return _blocked("finding_packet_unreadable")
    if str(packet.get("SECURITY_CASE_ID")) != case_id:
        return _blocked("case_mismatch")
    if str(packet.get("SECURITY_FINDING_ID")) != finding_id:
        return _blocked("finding_mismatch")
    if str(packet.get("TRIAGE_STATUS")) != "CONFIRMED":
        return _blocked("finding_not_confirmed")
    plane = str(packet.get("AFFECTED_PLANE") or "")
    if plane == "PRODUCT":
        return _not_applicable("wrong_plane_product", revision=revision)
    if plane == "INFRASTRUCTURE":
        return _not_applicable("wrong_plane_infrastructure", revision=revision)
    if plane != "CONTROL_PLANE":
        return _blocked("plane_unknown")
    cited = packet.get("EVIDENCE")
    if not isinstance(cited, list) or not cited:
        return _blocked("evidence_missing")
    first = cited[0]
    if not isinstance(first, dict):
        return _blocked("evidence_missing")
    cite_path = str(first.get("PATH") or packet.get("PATH") or "")
    quote = str(first.get("QUOTE") or "")
    if not cite_path or not quote:
        return _blocked("evidence_missing")
    source = _open_text(repo_root, cite_path)
    if isinstance(source, dict):
        return source
    remediating_quote = _opt(request, "remediating_quote")
    reviewer_id = _opt(request, "reviewer_id")
    remediator_id = _opt(request, "remediator_id")
    remediation_path = _opt(request, "remediation_path")
    still_vulnerable = quote in source
    remediated_observable = bool(remediating_quote) and remediating_quote in source
    if still_vulnerable and not remediated_observable:
        verdict = "FAIL"
        disposition = "STILL_VULNERABLE"
        reason = "vulnerable_snapshot_observable"
    elif remediated_observable and not still_vulnerable:
        if not reviewer_id:
            return _blocked("reviewer_id_required_for_pass")
        if remediator_id and remediator_id == reviewer_id:
            return _blocked("self_certification_forbidden")
        if remediation_path:
            receipt = _open_text(repo_root, remediation_path)
            if isinstance(receipt, dict):
                return receipt
            if finding_id not in receipt:
                return _blocked("remediation_receipt_unbound")
        verdict = "PASS"
        disposition = "REMEDIATED"
        reason = "independent_post_remediation_observation"
    else:
        return _blocked("unevaluable_snapshot")
    payload = {
        "schema_version": "target-control-plane-security-review-v1",
        "CONTRACT_ID": CONTRACT_ID,
        "SECURITY_CASE_ID": case_id,
        "SECURITY_FINDING_ID": finding_id,
        "AFFECTED_PLANE": "CONTROL_PLANE",
        "SOURCE_SIGNAL_HASH": packet.get("SOURCE_SIGNAL_HASH"),
        "REVISION": revision,
        "VERDICT": verdict,
        "FINDING_DISPOSITION": disposition,
        "REASON": reason,
        "REVIEWER_ID": reviewer_id or "none",
        "PRODUCT_SECURITY_VERDICT": "not_issued",
        "ARCHIVE_VERDICT": "not_issued",
        "CITED_PATH": cite_path,
    }
    written = _write_review(repo_root, case_id, finding_id, payload)
    if isinstance(written, dict):
        return written
    return {
        "STATUS": verdict,
        "REASON": reason,
        "FINDING_DISPOSITION": disposition,
        "PASS": "YES" if verdict == "PASS" else "NO",
        "REVIEW_PATH": written,
        "REVISION": revision,
        "SECURITY_CASE_ID": case_id,
        "SECURITY_FINDING_ID": finding_id,
        "CONTROL_PLANE_SECURITY_VERDICT_AUTHORITY": "YES",
        "PRODUCT_SECURITY_VERDICT_AUTHORITY": "NO",
        "PROCESS_WRITE_AUTHORITY": "NO",
    }


def _write_review(
    repo_root: Path, case_id: str, finding_id: str, payload: dict[str, object]
) -> str | dict[str, object]:
    relative = (
        Path("opsx")
        / "runs"
        / "security-cases"
        / case_id
        / "reviews"
        / "control-plane"
        / f"{finding_id}.md"
    )
    target = canonical_contained(repo_root / relative, repo_root)
    if target is None:
        return _blocked("path_outside_workspace")
    target.parent.mkdir(parents=True, exist_ok=True)
    body = (
        "# CONTROL_PLANE_SECURITY_REVIEW_PACKET\n\n"
        f"{REVIEW_BEGIN}"
        f"{json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True)}\n"
        f"{REVIEW_END}"
    )
    tmp = target.with_suffix(".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, target)
    return relative.as_posix()


def _open_text(repo_root: Path, relative: str) -> str | dict[str, object]:
    resolved = canonical_contained(Path(relative), repo_root)
    if resolved is None or not resolved.is_file():
        return _blocked("path_outside_workspace")
    data = resolved.read_bytes()
    if len(data) > 1_000_000:
        return _blocked("file_too_large")
    return data.decode("utf-8")


def _parse_finding(text: str) -> dict[str, object] | None:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    start = text.find(PACKET_BEGIN)
    end = text.find(PACKET_END)
    if start < 0 or end < 0 or end <= start:
        return None
    blob = text[start + len(PACKET_BEGIN) : end]
    try:
        value = json.loads(blob)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _not_applicable(reason: str, *, revision: str) -> dict[str, object]:
    return {
        "STATUS": "NOT_APPLICABLE",
        "REASON": reason,
        "PASS": "NO",
        "FINDING_DISPOSITION": "OUT_OF_SCOPE",
        "REVISION": revision,
        "NEXT": (
            "/opsx-security-review"
            if "product" in reason
            else "BLOCKED_AUTHORITY_GAP"
        ),
    }


def _projectization_block(repo_root: Path) -> dict[str, object] | None:
    from opsx_runtime_gates import projectization_and_adoption_block

    return projectization_and_adoption_block(
        repo_root,
        "python.pipeline.control_plane_security_review",
        blocked_factory=_blocked,
    )


def _opt(request: dict[str, object], key: str) -> str:
    value = request.get(key)
    return value.strip() if isinstance(value, str) else ""


def _blocked(reason: str, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "STATUS": "BLOCKED",
        "REASON": reason,
        "PASS": "NO",
        "CONTROL_PLANE_SECURITY_VERDICT_AUTHORITY": "YES",
        "PRODUCT_SECURITY_VERDICT_AUTHORITY": "NO",
        "PROCESS_WRITE_AUTHORITY": "NO",
        "GREEN_WRITER": "NO",
        "ARCHIVE_VERDICT_WRITER": "NO",
    }
    payload.update(extra)
    return payload


def _seal(result: dict[str, object]) -> dict[str, object]:
    sealed = dict(result)
    sealed.setdefault("CONTRACT_ID", CONTRACT_ID)
    sealed.setdefault("SOURCE_PARITY", "ADAPTED")
    sealed.setdefault("ARCHIVE_VERDICT", "not_issued")
    sealed.setdefault("PRODUCT_SECURITY_VERDICT", "not_issued")
    sealed.setdefault("COMMIT_ALLOWED", "NO")
    if sealed.get("STATUS") not in VERDICTS:
        sealed["STATUS"] = "BLOCKED"
    return sealed


def packet_identity(payload: dict[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
