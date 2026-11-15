"""Offline, bounded recovery of committed historical single-row SDK answers.

No SDK calls. A salvage record is *not* itself evidence: re-read the exact
original SDK conversation and terminal metadata, attest its Git blob, then
invoke the native fail-closed row recovery writer. Always preflight the entire
index before any writes. Aggregate PASS is never claimed here.
"""
from __future__ import annotations

from opsx.project_context import resource_path

import argparse
import json
from pathlib import Path
from typing import Any

from agents.verify.scenario_coverage_verify.row_recovery import (
    _final_response, _git, recover,
)
from agents.verify.scenario_coverage_verify.evidence_contract import input_snapshot

INDEX_CONTRACT = "scenario_sdk_salvage_index_v0"
CANDIDATE_CONTRACT = "scenario_sdk_salvage_candidate_v0"


def _inside(root: Path, rel: str, prefix: str | tuple[str, ...]) -> Path:
    prefixes = (prefix,) if isinstance(prefix, str) else prefix
    if not isinstance(rel, str) or not any(rel.startswith(p) for p in prefixes):
        raise ValueError("REPLAY_PATH_OUTSIDE_AUTHORIZED_SCOPE")
    candidate = resource_path(root, rel)
    if candidate.is_symlink():
        raise ValueError("REPLAY_PATH_UNSAFE")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError("REPLAY_PATH_UNSAFE")
    if not candidate.is_file():
        raise ValueError("REPLAY_PATH_MISSING")
    return candidate.resolve()


def _metadata_for_source(root: Path, change_id: str, execution_id: str,
                         source: str) -> Path:
    base = resource_path(root, "opsx", "runs", "dialogs", change_id)
    base = base / execution_id / "scenario_coverage_verify"
    if base.is_symlink() or not base.is_dir():
        raise ValueError("REPLAY_METADATA_DIRECTORY_MISSING")
    candidates = [base / "meta.json", *sorted(base.glob("revisions/*/meta.json"))]
    matches = []
    for item in candidates:
        if not item.is_file() or item.is_symlink():
            continue
        if not item.resolve().is_relative_to(root):
            raise ValueError("REPLAY_METADATA_PATH_UNSAFE")
        metadata = json.loads(item.read_text(encoding="utf-8"))
        original_path = metadata.get("legacy_conversation_json")
        if isinstance(original_path, str) and original_path.endswith("/" + source):
            matches.append(item)
    if len(matches) != 1:
        raise ValueError("REPLAY_METADATA_NOT_UNIQUE")
    return matches[0]


def replay_saved(*, root: Path, change_id: str, index_path: Path,
                 write: bool = False, model_options: list[Any] | None = None
                 ) -> dict[str, Any]:
    root = root.resolve()
    if index_path.is_symlink():
        raise ValueError("REPLAY_INDEX_SYMLINK")
    index_path = index_path.resolve()
    parent_posix = index_path.parent.as_posix()
    legacy_recovery = (
        resource_path(root, "openspec", "changes", change_id, "verify", "scenario-recovery-")
    ).as_posix()
    modern_recovery = (resource_path(root, "opsx", "receipts", change_id, "scenario-recovery-")).as_posix()
    index_ok = (
        index_path.name == "INDEX.json"
        and index_path.is_relative_to(root)
        and not index_path.is_symlink()
        and (
            parent_posix.startswith(legacy_recovery)
            or parent_posix.startswith(modern_recovery)
        )
    )
    if not index_ok:
        raise ValueError("REPLAY_INDEX_UNSAFE")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    execution_id = index.get("execution_id")
    anchor = index.get("captured_from_git_commit")
    if (index.get("contract") != INDEX_CONTRACT
            or index.get("gate_authority") != "NONE"
            or index.get("change_id") != change_id
            or not isinstance(execution_id, str)
            or not execution_id or "/" in execution_id or "\\" in execution_id
            or not isinstance(anchor, str) or len(anchor) != 40):
        raise ValueError("REPLAY_INDEX_IDENTITY_INVALID")
    entries = index.get("records")
    if (not isinstance(entries, list) or not entries
            or len(entries) != index.get("rows_completed")
            or len(entries) != len({x.get("source") for x in entries if isinstance(x, dict)})):
        raise ValueError("REPLAY_INDEX_ROWS_INVALID")

    source_prefixes = (
        f"openspec/changes/{change_id}/verify/scenario-row-streams/{execution_id}/",
        f"opsx/logs/{change_id}/legacy/scenario-row-streams/{execution_id}/",
    )
    candidate_prefixes = (
        f"openspec/changes/{change_id}/verify/scenario-recovery-{execution_id}/rows/",
        f"opsx/receipts/{change_id}/scenario-recovery-{execution_id}/rows/",
    )
    planned = []
    scenario_ids = set()
    before = input_snapshot(root, change_id)["source_digest"]
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("REPLAY_ENTRY_INVALID")
        original = _inside(root, entry.get("source"), source_prefixes)
        record = _inside(root, entry.get("candidate"), candidate_prefixes)
        candidate = json.loads(record.read_text(encoding="utf-8"))
        if (candidate.get("contract") != CANDIDATE_CONTRACT
                or candidate.get("gate_authority") != "NONE"
                or candidate.get("change_id") != change_id
                or candidate.get("execution_id") != execution_id
                or candidate.get("source_revision_at_capture") != anchor
                or candidate.get("conversation_path") != entry["source"]):
            raise ValueError("REPLAY_CANDIDATE_IDENTITY_INVALID")
        observed_sha = _git(root, "hash-object", str(original)).decode().strip()
        pinned_sha = _git(root, "rev-parse", f"{anchor}:{entry['source']}").decode().strip()
        if (observed_sha != candidate.get("original_conversation_git_blob_sha")
                or observed_sha != pinned_sha):
            raise ValueError("REPLAY_ORIGINAL_BLOB_CHANGED")
        answer = _final_response(original)
        if answer != candidate.get("full_sdk_answer"):
            raise ValueError("REPLAY_CANDIDATE_DIFFERS_FROM_ORIGINAL")
        reviews = answer.get("row_reviews")
        if not isinstance(reviews, list) or len(reviews) != 1:
            raise ValueError("REPLAY_NOT_SINGLE_ROW")
        sid = reviews[0].get("scenario_id")
        if not isinstance(sid, str) or sid in scenario_ids:
            raise ValueError("REPLAY_DUPLICATE_SCENARIO")
        scenario_ids.add(sid)
        metadata = _metadata_for_source(root, change_id, execution_id, entry["source"])
        relative_meta = metadata.relative_to(root).as_posix()
        pinned_meta = _git(root, "rev-parse", f"{anchor}:{relative_meta}").decode().strip()
        observed_meta = _git(root, "hash-object", str(metadata)).decode().strip()
        if pinned_meta != observed_meta:
            raise ValueError("REPLAY_METADATA_BLOB_CHANGED")
        # Preflight every input and every native proof before the first write.
        preview = recover(
            root=root, change_id=change_id, anchor=anchor,
            conversation_path=original, meta_path=metadata,
            write=False, model_options=model_options,
        )
        planned.append((original, metadata, preview))

    if input_snapshot(root, change_id)["source_digest"] != before:
        raise ValueError("REPLAY_SOURCE_CHANGED_DURING_PREFLIGHT")
    reports = []
    if write:
        for original, metadata, _ in planned:
            try:
                reports.append(recover(
                    root=root, change_id=change_id, anchor=anchor,
                    conversation_path=original, meta_path=metadata,
                    write=True, model_options=model_options,
                ))
            except Exception as exc:
                # Previous rows may be committed. Never report a full success.
                return {
                    "status": "PARTIAL_RECOVERY_PAUSE",
                    "sdk_calls": 0, "aggregate_verdict": "NOT_ASSERTED",
                    "completed_rows": len(reports), "requested_rows": len(planned),
                    "failure": f"{type(exc).__name__}: {exc}",
                    "results": reports,
                }
    else:
        reports = [preview for _, _, preview in planned]
    rejected = {
        key: value
        for report in reports
        for key, value in report["rejected_supported_proofs"].items()
    }
    return {
        "status": (
            "RECOVERED_WITH_REJECTIONS" if write and rejected
            else "RECOVERED" if write else "DRY_RUN"
        ),
        "sdk_calls": 0,
        "aggregate_verdict": "NOT_ASSERTED",
        "requested_rows": len(planned),
        "counts": {
            kind: sum(r["counts"][kind] for r in reports)
            for kind in ("SUPPORTED", "GAP", "INSPECTION_BLOCKED", "CONTRACT_CONFLICT")
        },
        "eligible_supported_receipts": sum(
            r["eligible_supported_receipts"] for r in reports
        ),
        "rejected_supported_proofs": rejected,
        "written": {
            "observations": sum(r["written"]["observations"] for r in reports),
            "supported_receipts": sum(r["written"]["supported_receipts"] for r in reports),
        },
        "results": reports,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay committed SDK rows without SDK calls")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--change", required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--write", action="store_true", help="Explicitly publish native receipts")
    args = parser.parse_args()
    root = args.repo.resolve()
    path = args.index if args.index.is_absolute() else resource_path(root, args.index)
    result = replay_saved(root=root, change_id=args.change, index_path=path,
                          write=args.write)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
