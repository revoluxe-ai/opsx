#!/usr/bin/env python3
"""Mandatory Architecture -> Technical Design boundary for every OPSX change.

Machine-checks structure and freshness of a separately produced review receipt.
This does NOT independently establish the reviewer's identity or semantic quality.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

SCHEMA_VERSION = "opsx-architecture-review-v1"
REQUIRED_SECTIONS = (
    "Context and constraints",
    "Boundaries and ownership",
    "Decisions and rationale",
    "Data flow and interfaces",
    "Invariants and failure modes",
    "Compatibility and migration",
    "Technical design handoff",
)
CHANGE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,127}$")
READY = re.compile(r"(?m)^ARCHITECTURE_STATUS:\s*READY\s*$")
UNRESOLVED = re.compile(r"(?im)\b(TODO|TBD|FIXME|DECIDE LATER)\b")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _change(root: Path, change_id: str) -> Path:
    if not CHANGE_ID.fullmatch(change_id or ""):
        raise ValueError("CHANGE_ID_INVALID")
    change = root / "openspec" / "changes" / change_id
    if not _inside(root, change):
        raise ValueError("CHANGE_PATH_OUTSIDE_ROOT")
    if change.is_symlink():
        raise ValueError("CHANGE_SYMLINK_FORBIDDEN")
    return change


def _upstream(change: Path) -> tuple[str, list[str]]:
    required = [change / "owner-contract.md", change / "proposal.md"]
    candidates = required + sorted((change / "specs").rglob("*.md")) + sorted(
        (change / "decisions").rglob("*.md")
    )
    findings: list[str] = []
    h = hashlib.sha256()
    for path in candidates:
        if not _inside(change, path) or path.is_symlink():
            findings.append("UPSTREAM_PATH_UNTRUSTED:" + str(path))
            continue
        if not path.is_file():
            findings.append("UPSTREAM_MISSING:" + path.name)
            continue
        data = path.read_bytes()
        rel = path.relative_to(change).as_posix()
        h.update(rel.encode("utf-8") + b"\0" + sha256(data).encode("ascii") + b"\n")
    if not (change / "specs").is_dir() or not list((change / "specs").rglob("*.md")):
        # OpenSpec may intentionally skip specs; that choice remains upstream authority.
        # Proposal and reviewed Owner Contract remain required in all cases.
        pass
    return h.hexdigest(), findings


def _architecture(change: Path) -> tuple[str | None, list[str]]:
    path = change / "architecture.md"
    if path.is_symlink() or not _inside(change, path):
        return None, ["ARCHITECTURE_PATH_UNTRUSTED"]
    if not path.is_file():
        return None, ["ARCHITECTURE_MISSING"]
    data = path.read_bytes()
    text = data.decode("utf-8")
    findings = []
    if not READY.search(text):
        findings.append("ARCHITECTURE_NOT_READY")
    heads = list(re.finditer(r"(?m)^## (.+?)\s*$", text))
    sections = {m.group(1).strip(): text[m.end(): heads[i + 1].start() if i + 1 < len(heads) else len(text)].strip()
                for i, m in enumerate(heads)}
    for name in REQUIRED_SECTIONS:
        if len(sections.get(name, "")) < 25:
            findings.append("ARCHITECTURE_SECTION_MISSING_OR_EMPTY:" + name)
    if UNRESOLVED.search(text):
        findings.append("ARCHITECTURE_UNRESOLVED_PLACEHOLDER")
    return sha256(data), findings


def inspect(root: Path, change_id: str, *, only_if_downstream: bool = False,
            validate_design_binding: bool = True) -> dict:
    change = _change(root.resolve(), change_id)
    if only_if_downstream and not any((change / f).is_file() for f in ("design.md", "tasks.md")):
        return {"status": "NOT_YET_DUE", "change_id": change_id, "findings": []}
    upstream_digest, findings = _upstream(change)
    architecture_digest, gaps = _architecture(change)
    findings += gaps
    # A READY Technical Design is bound to the exact accepted architecture.
    # Re-reviewing revised architecture must not silently revalidate stale design.
    design_path = change / "design.md"
    if not _inside(change, design_path) or design_path.is_symlink():
        findings.append("DESIGN_PATH_UNTRUSTED")
    elif validate_design_binding and design_path.is_file():
        design_text = design_path.read_text(encoding="utf-8")
        if re.search(r"(?m)^DESIGN_STATUS:\s*READY\s*$", design_text):
            match = re.search(r"(?m)^ARCHITECTURE_SHA256:\s*([a-f0-9]{64})\s*$", design_text)
            if match is None or match.group(1) != architecture_digest:
                findings.append("DESIGN_ARCHITECTURE_STALE")
    receipt_path = change / "architecture-evidence" / "ARCHITECTURE-REVIEW.json"
    if not _inside(change, receipt_path) or receipt_path.is_symlink():
        findings.append("REVIEW_PATH_UNTRUSTED")
    elif not receipt_path.is_file():
        findings.append("ARCHITECTURE_REVIEW_MISSING")
    else:
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if not isinstance(receipt, dict):
                raise ValueError("not an object")
            fields = {
                "schema_version": SCHEMA_VERSION,
                "change_id": change_id,
                "architecture_sha256": architecture_digest,
                "upstream_sha256": upstream_digest,
                "verdict": "ACCEPTED",
            }
            for key, expected in fields.items():
                if receipt.get(key) != expected:
                    findings.append("REVIEW_IDENTITY_OR_FRESHNESS_MISMATCH:" + key)
            author, reviewer = receipt.get("author_id"), receipt.get("reviewer_id")
            if not isinstance(author, str) or not author.strip() or not isinstance(reviewer, str) or not reviewer.strip() or author.strip() == reviewer.strip():
                findings.append("REVIEWER_SEPARATION_UNPROVEN")
            if not isinstance(receipt.get("review_execution_id"), str) or not receipt["review_execution_id"].strip():
                findings.append("REVIEW_EXECUTION_ID_MISSING")
            if receipt.get("findings") != []:
                findings.append("REVIEW_UNRESOLVED_FINDINGS")
        except (OSError, ValueError, UnicodeError) as exc:
            findings.append("ARCHITECTURE_REVIEW_INVALID:" + type(exc).__name__)
    return {
        "status": "READY" if not findings else "BLOCKED",
        "change_id": change_id,
        "architecture_sha256": architecture_digest,
        "upstream_sha256": upstream_digest,
        "findings": findings,
    }


def record_review(root: Path, change_id: str, *, author_id: str, reviewer_id: str,
                  reviewed_sha256: str, review_execution_id: str) -> dict:
    """Persist outcome only AFTER an independent review outside this checker.

    The caller attests reviewer/author identity; this process cannot authenticate
    that identity. Policy requires separate reviewers, not self-issued approval.
    """
    change = _change(root.resolve(), change_id)
    upstream_digest, findings = _upstream(change)
    architecture_digest, gaps = _architecture(change)
    findings += gaps
    if not re.fullmatch(r"[a-f0-9]{64}", reviewed_sha256 or "") or architecture_digest != reviewed_sha256:
        findings.append("REVIEWED_REVISION_STALE")
    if not review_execution_id.strip():
        findings.append("REVIEW_EXECUTION_ID_MISSING")
    if not author_id.strip() or not reviewer_id.strip() or author_id.strip() == reviewer_id.strip():
        findings.append("REVIEWER_SEPARATION_UNPROVEN")
    if findings:
        return {"status": "BLOCKED", "findings": findings}
    evidence = {
        "schema_version": SCHEMA_VERSION,
        "change_id": change_id,
        "architecture_sha256": architecture_digest,
        "upstream_sha256": upstream_digest,
        "verdict": "ACCEPTED",
        "author_id": author_id.strip(),
        "reviewer_id": reviewer_id.strip(),
        "review_execution_id": review_execution_id.strip(),
        "findings": [],
    }
    folder = change / "architecture-evidence"
    if not _inside(change, folder) or folder.is_symlink():
        return {"status": "BLOCKED", "findings": ["REVIEW_PATH_UNTRUSTED"]}
    folder.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         dir=folder, delete=False) as handle:
            temp = Path(handle.name)
            json.dump(evidence, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, folder / "ARCHITECTURE-REVIEW.json")
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
    return inspect(root, change_id, validate_design_binding=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inputs", "check", "check-if-design", "record-review"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--change", required=True)
    parser.add_argument("--author-id")
    parser.add_argument("--reviewer-id")
    parser.add_argument("--reviewed-sha256")
    parser.add_argument("--review-execution-id")
    args = parser.parse_args(argv)
    try:
        if args.action == "inputs":
            change = _change(args.root.resolve(), args.change)
            digest, findings = _upstream(change)
            arch_digest, gaps = _architecture(change)
            result = {"status": "INSPECTABLE" if not findings + gaps else "BLOCKED",
                      "change_id": args.change, "architecture_sha256": arch_digest,
                      "upstream_sha256": digest, "findings": findings + gaps}
        elif args.action == "record-review":
            if not all((args.author_id, args.reviewer_id, args.reviewed_sha256, args.review_execution_id)):
                raise ValueError("REVIEW_FIELDS_REQUIRED")
            result = record_review(args.root, args.change, author_id=args.author_id,
                                   reviewer_id=args.reviewer_id,
                                   reviewed_sha256=args.reviewed_sha256,
                                   review_execution_id=args.review_execution_id)
        else:
            result = inspect(args.root, args.change,
                             only_if_downstream=args.action == "check-if-design")
    except (ValueError, OSError, UnicodeError) as exc:
        result = {"status": "BLOCKED", "change_id": args.change,
                  "findings": [str(exc)]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in ("READY", "NOT_YET_DUE", "INSPECTABLE") else 1


if __name__ == "__main__":
    raise SystemExit(main())
