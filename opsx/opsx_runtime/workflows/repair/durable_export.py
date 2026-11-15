"""Durable on-disk exports for repair missions (findings, hunt, fan-in, learning).

Пишет структурированные JSON под ``<output_dir>/durable/`` и зеркала имён
bug-team (``family/``, ``results/``), чтобы данные миссии не терялись в одном
``latest_state.json`` и их было легко найти (``durable/INDEX.json``).

Не заменяет canonical ``opsx/runs/bug-cases/`` — это mission-local SoT до
моста в bug-team learning runtime.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schemas import RepairState

EXPORT_SCHEMA_VERSION = 1

_OWNER_FINAL_TO_VERDICT: dict[str, str] = {
    "PROMOTE_CLASS_LESSON": "CLASS_LESSON_CANDIDATE",
    "KEEP_LESSON_CANDIDATE_ONLY": "LOCAL_CASE_NOTE_ONLY",
    "CLOSE_EPISODE_NO_CLASS_LESSON": "NO_REUSABLE_LESSON",
    "SPLIT_NEW_EPISODE": "OWNER_DECISION_REQUIRED",
    "STOP": "LEARNING_BLOCKED",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_json(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return _sha256_bytes(text.encode("utf-8"))


def _write_json(path: Path, value: Any) -> str:
    """Записать JSON; вернуть sha256 содержимого."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n"
    path.write_text(text, encoding="utf-8")
    return _sha256_bytes(text.encode("utf-8"))


def _rel(output_dir: Path, path: Path) -> str:
    try:
        return path.relative_to(output_dir).as_posix()
    except ValueError:
        return path.as_posix()


def _worker_manifest(output_dir: Path) -> list[dict[str, Any]]:
    workers = output_dir / "workers"
    if not workers.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(workers.iterdir()):
        if not path.is_file():
            continue
        rows.append(
            {
                "name": path.name,
                "path": _rel(output_dir, path),
                "sha256": _sha256_bytes(path.read_bytes()),
                "kind": path.suffix.lstrip("."),
            }
        )
    return rows


def build_learning_decision(state: RepairState, *, fanin_rel: str | None, fanin_sha: str | None) -> dict[str, Any]:
    """Черновик LEARNING_DECISION совместимый по полям с bug-team contract."""
    invariant_id = state.invariants[0].invariant_id if state.invariants else None
    owner = (state.owner_final or "").strip().upper()
    verdict = state.learning.get("LEARNING_VERDICT")
    if not verdict and owner:
        verdict = _OWNER_FINAL_TO_VERDICT.get(owner, "OWNER_DECISION_REQUIRED")
    if not verdict:
        verdict = "OWNER_DECISION_REQUIRED" if state.fanin else "LEARNING_BLOCKED"

    promotion = state.learning.get("PROMOTION_DECISION")
    if promotion is None:
        if verdict in {"CLASS_LESSON_CANDIDATE", "SUPERCLASS_SEED_EVIDENCE", "COMPLIANCE_ENFORCEMENT_CANDIDATE"}:
            promotion = "QUEUED" if owner == "PROMOTE_CLASS_LESSON" else "DEFERRED"
        else:
            promotion = "NONE"

    stage_invoked = bool(state.learning.get("LEARNING_STAGE_INVOKED"))
    if state.learning.get("family_learning_invoked"):
        stage_invoked = True
    synthesized = bool(owner) and not stage_invoked

    learning_status = state.learning.get("LEARNING_STATUS")
    if not learning_status:
        if state.error:
            learning_status = "BLOCKED"
        elif synthesized and state.fanin and state.fanin.status == "FANIN_READY":
            learning_status = "COMPLETE"
        elif state.fanin:
            learning_status = "OWNER_WAIT"
        else:
            learning_status = "BLOCKED"

    full_packet = verdict in {
        "COMPLIANCE_ENFORCEMENT_CANDIDATE",
        "CLASS_LESSON_CANDIDATE",
        "SUPERCLASS_SEED_EVIDENCE",
    }
    recommendations = state.learning.get("RECOMMENDATIONS")
    if not isinstance(recommendations, dict):
        recommendations = {
            "STATIC_CHECK_CANDIDATE": verdict == "COMPLIANCE_ENFORCEMENT_CANDIDATE",
            "PROJECT_WIDE_HUNT_REQUEST": bool(state.hunts),
            "TESTING_RULES_CANDIDATE": verdict == "CLASS_LESSON_CANDIDATE",
        }

    body: dict[str, Any] = {
        "EXPORT_SCHEMA_VERSION": EXPORT_SCHEMA_VERSION,
        "SYNTHESIS_NOTE": (
            "Synthesized from LangGraph repair mission state; "
            "FAMILY_LEARNING_WORKER may not have run."
            if synthesized
            else None
        ),
        "LEARNING_DECISION_ID": state.learning.get(
            "LEARNING_DECISION_ID",
            f"LD-{state.mission_id}",
        ),
        "CASE_ID": state.learning.get("CASE_ID", state.mission_id),
        "BUG_FAMILY_ID": state.learning.get("BUG_FAMILY_ID", invariant_id),
        "INVARIANT_ID": invariant_id,
        "LEARNING_STAGE_INVOKED": stage_invoked or synthesized,
        "LEARNING_VERDICT": verdict,
        "POLICY_ALREADY_EXISTS": state.learning.get("POLICY_ALREADY_EXISTS"),
        "POLICY_REFS": state.learning.get("POLICY_REFS") or [],
        "ENFORCEMENT_GAP": state.learning.get("ENFORCEMENT_GAP"),
        "PROMOTION_DECISION": promotion,
        "RECOMMENDATIONS": recommendations,
        "FULL_PACKET_REQUIRED": full_packet,
        "FULL_PACKET_PATH": state.learning.get("FULL_PACKET_PATH"),
        "SOURCE_FANIN_PATH": fanin_rel,
        "SOURCE_FANIN_HASH": fanin_sha,
        "SOURCE_ARTIFACT_PATHS": state.learning.get("SOURCE_ARTIFACT_PATHS") or [],
        "SOURCE_ARTIFACT_HASHES": state.learning.get("SOURCE_ARTIFACT_HASHES") or [],
        "LEARNING_STATUS": learning_status,
        "DECISION_RATIONALE": state.learning.get(
            "DECISION_RATIONALE",
            f"owner_final={state.owner_final!r}; mission_status={state.status}",
        ),
        "CREATED_AT": state.learning.get("CREATED_AT", _utc_now()),
        "OWNER_FINAL": state.owner_final,
        "MISSION_STATUS": state.status,
    }
    body["DECISION_HASH"] = _sha256_json(
        {k: v for k, v in body.items() if k != "DECISION_HASH"}
    )
    return body


def persist_durable_exports(state: RepairState) -> dict[str, Any]:
    """Записать все durable-артефакты; вернуть manifest для INDEX."""
    output_dir = Path(state.output_dir)
    durable = output_dir / "durable"
    family = output_dir / "family"
    results = output_dir / "results"

    manifest: dict[str, str] = {}
    written_at = _utc_now()

    finished_path = durable / "findings" / "FINISHED.json"
    manifest["findings_finished"] = _rel(
        output_dir,
        finished_path,
    )
    _write_json(
        finished_path,
        {
            "schema_version": EXPORT_SCHEMA_VERSION,
            "mission_id": state.mission_id,
            "seen": state.seen,
            "finished": [o.model_dump(mode="json") for o in state.finished],
        },
    )

    queue_path = durable / "findings" / "QUEUE.json"
    manifest["findings_queue"] = _rel(output_dir, queue_path)
    _write_json(
        queue_path,
        {
            "schema_version": EXPORT_SCHEMA_VERSION,
            "active": state.active.model_dump(mode="json") if state.active else None,
            "queue": [f.model_dump(mode="json") for f in state.queue],
            "rejects": state.rejects,
        },
    )

    if state.source_review is not None:
        src_path = durable / "findings" / "SOURCE_REVIEW.json"
        manifest["source_review"] = _rel(output_dir, src_path)
        _write_json(
            src_path,
            state.source_review.model_dump(mode="json"),
        )

    if state.invariants:
        inv_payload = state.invariants[0].model_dump(mode="json")
        inv_durable = durable / "invariant" / "ROOT_INVARIANT.json"
        inv_family = family / "INVARIANT.json"
        sha = _write_json(inv_durable, inv_payload)
        manifest["invariant"] = _rel(output_dir, inv_durable)
        _write_json(inv_family, inv_payload)
        manifest["family_invariant"] = _rel(output_dir, inv_family)
        _ = sha

    if state.hunts:
        hunts_path = durable / "hunt" / "HUNTS.json"
        manifest["hunts"] = _rel(output_dir, hunts_path)
        _write_json(
            hunts_path,
            {
                "schema_version": EXPORT_SCHEMA_VERSION,
                "hunt_rounds": state.hunt_rounds,
                "hunts": [h.model_dump(mode="json") for h in state.hunts],
            },
        )

    if state.relations:
        rel_path = durable / "hunt" / "RELATIONS.json"
        manifest["relations"] = _rel(output_dir, rel_path)
        _write_json(
            rel_path,
            [r.model_dump(mode="json") for r in state.relations],
        )

    fanin_rel: str | None = None
    fanin_sha: str | None = None
    if state.fanin is not None:
        fanin_payload = state.fanin.model_dump(mode="json")
        fanin_durable = durable / "fanin" / "FANIN.json"
        fanin_results = results / "FANIN.json"
        fanin_sha = _write_json(
            fanin_durable,
            {"schema_version": EXPORT_SCHEMA_VERSION, **fanin_payload},
        )
        fanin_rel = _rel(output_dir, fanin_durable)
        manifest["fanin"] = fanin_rel
        _write_json(fanin_results, fanin_payload)
        manifest["results_fanin"] = _rel(output_dir, fanin_results)

    learning = build_learning_decision(
        state,
        fanin_rel=fanin_rel,
        fanin_sha=fanin_sha,
    )
    learn_durable = durable / "learning" / "LEARNING_DECISION.json"
    learn_family = family / "LEARNING_DECISION.json"
    learn_results = results / "LEARNING_DECISION.json"
    manifest["learning_decision"] = _rel(output_dir, learn_durable)
    _write_json(learn_durable, learning)
    if family.exists() or state.invariants:
        family.mkdir(parents=True, exist_ok=True)
        _write_json(learn_family, learning)
        manifest["family_learning_decision"] = _rel(output_dir, learn_family)
    results.mkdir(parents=True, exist_ok=True)
    _write_json(learn_results, learning)
    manifest["results_learning_decision"] = _rel(output_dir, learn_results)

    workers_path = durable / "workers" / "MANIFEST.json"
    worker_rows = _worker_manifest(output_dir)
    manifest["workers_manifest"] = _rel(output_dir, workers_path)
    _write_json(
        workers_path,
        {
            "schema_version": EXPORT_SCHEMA_VERSION,
            "mission_id": state.mission_id,
            "files": worker_rows,
        },
    )

    summary_path = durable / "MISSION_SNAPSHOT.json"
    manifest["mission_snapshot"] = _rel(output_dir, summary_path)
    _write_json(
        summary_path,
        {
            "schema_version": EXPORT_SCHEMA_VERSION,
            "mission_id": state.mission_id,
            "status": state.status,
            "current_stage": state.current_stage,
            "error": state.error,
            "revision": state.revision,
            "owner_final": state.owner_final,
            "learning": state.learning,
            "source_report_path": state.source_report_path,
            "commit_sha": state.commit_sha,
        },
    )

    index_path = durable / "INDEX.json"
    index_body = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "mission_id": state.mission_id,
        "updated_at": written_at,
        "status": state.status,
        "current_stage": state.current_stage,
        "latest_state": "latest_state.json",
        "artifacts": manifest,
        "worker_receipt_count": len(worker_rows),
        "contract_hints": {
            "bug_team_fanin_md": ".cursor/pipeline/contracts/bug-episode/FANIN.md",
            "bug_team_learning": ".cursor/pipeline/bug-team/contracts/LEARNING_DECISION.md",
        },
    }
    _write_json(index_path, index_body)
    return index_body
