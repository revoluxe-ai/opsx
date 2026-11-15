"""Atomic, individually source-bound scenario review records.

One frozen scenario => one durable observation; only SUPPORTED observations
publish PASS receipts. Validated GAP/BLOCKED observations are reusable negative
review results with independent freshness checks, never PASS authority.
No dependency on a completed whole-plan model response.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping

from agents.check_receipts import best_effort_write_pass, read_pass
from workflows.artifact_paths import receipts_dir
from agents.verify.scenario_coverage_verify.evidence_contract import review_proof_error
from agents.verify.scenario_coverage_verify.delta_rows import ROW_CONTRACT
from agents.verify.scenario_coverage_verify.schemas import (
    ScenarioCoverageGap, ScenarioCoverageRowReview,
)

ROW_PASS_CONTRACT = "scenario_supported_row_v1"
ROW_PASS_VERDICT = "ROW_SUPPORTED"
OBSERVATION_CONTRACT = "scenario_row_observation_v1"
_MAX_OBSERVATION = 256 * 1024


def _product_row_identity(row_identity_contract: str) -> bool:
    """True when reuse keys only spec Scenario text and mapped test files."""
    return row_identity_contract == ROW_CONTRACT


def _json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def row_key(scenario_id: str) -> str:
    if not isinstance(scenario_id, str) or not scenario_id.strip():
        raise ValueError("ROW_ID_UNBOUND")
    return hashlib.sha256(scenario_id.encode("utf-8")).hexdigest()


def row_check(scenario_id: str) -> str:
    return "scenario_supported_" + row_key(scenario_id)


def _observation_path(root: Path, change_id: str, scenario_id: str) -> Path:
    change = root.resolve() / "openspec" / "changes" / change_id
    if change.resolve() != change or not change.is_dir():
        raise ValueError("ROW_OBSERVATION_CHANGE_UNSAFE")
    directory = receipts_dir(root, change_id) / "scenario-rows"
    directory = directory.resolve()
    if (
        directory.is_symlink()
        or not directory.resolve().is_relative_to(root.resolve())
    ):
        raise ValueError("ROW_OBSERVATION_DIRECTORY_UNSAFE")
    directory.mkdir(parents=True, exist_ok=True)
    return directory / (row_key(scenario_id) + ".json")


def write_observation(
    root: Path,
    *,
    change_id: str,
    row: ScenarioCoverageRowReview,
    row_digest: str | None,
    source_digest: str,
    plan_evidence_id: str,
    origin_execution_id: str,
    evidence_id: str,
    shared_digest: str | None = None,
    source_artifact_digest: str | None = None,
    recovery_review_mode: str = "",
    row_identity_contract: str = "",
    problem: str = "",
    gap_class: str = "",
    gaps: list[ScenarioCoverageGap] | None = None,
) -> None:
    """Atomically publish one review; status cannot be interpreted as PASS."""
    if not all(
        isinstance(x, str) and x.strip()
        for x in (source_digest, plan_evidence_id, origin_execution_id, evidence_id)
    ):
        raise ValueError("ROW_OBSERVATION_IDENTITY_UNBOUND")
    if gaps and (row.assessment != "GAP" or any(
        gap.scenario_id != row.scenario_id for gap in gaps
    )):
        raise ValueError("ROW_OBSERVATION_GAPS_IDENTITY_MISMATCH")
    path = _observation_path(root, change_id, row.scenario_id)
    if path.is_symlink():
        raise ValueError("ROW_OBSERVATION_SYMLINK")
    body: dict[str, Any] = {
        "contract": OBSERVATION_CONTRACT,
        "change_id": change_id,
        "scenario_id": row.scenario_id,
        "assessment": row.assessment,
        # Diagnostic verdict is not a reusable PASS proof. Only a separate
        # source-bound check-receipt can authorize aggregate row reuse.
        "result": (
            "PASS" if row.assessment == "SUPPORTED"
            else "FAIL" if row.assessment == "GAP" else "PAUSE"
        ),
        "problem": str(problem or row.evidence if row.assessment != "SUPPORTED" else ""),
        "gap_class": str(gap_class or ""),
        "gaps": [gap.model_dump(mode="json") for gap in (gaps or [])],
        "row_digest": row_digest,
        # The shared row scope binds model, verifier policy, plan and all
        # non-isolated inputs. Historical observations lacking it remain
        # diagnostic-only and must not be reused.
        "shared_digest": shared_digest or "",
        "source_digest": source_digest,
        "plan_evidence_id": plan_evidence_id,
        "origin_execution_id": origin_execution_id,
        "evidence_id": evidence_id,
        "source_artifact_digest": source_artifact_digest or "",
        "recovery_review_mode": recovery_review_mode,
        "row_identity_contract": row_identity_contract,
        "row": row.model_dump(mode="json"),
    }
    encoded = _json({
        **body,
        "checksum": hashlib.sha256(_json(body)).hexdigest(),
    }) + b"\n"
    if len(encoded) > _MAX_OBSERVATION:
        raise ValueError("ROW_OBSERVATION_TOO_LARGE")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb", dir=path.parent, delete=False,
        ) as fp:
            temporary = fp.name
            fp.write(encoded)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def read_observation(root: Path, *, change_id: str, scenario_id: str) -> dict | None:
    """Read checksum-valid observation for reporting, never as gate authority."""
    try:
        path = _observation_path(root, change_id, scenario_id)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_OBSERVATION:
            return None
        item = json.loads(path.read_bytes())
        if not isinstance(item, dict):
            return None
        checksum = item.pop("checksum", None)
        if checksum != hashlib.sha256(_json(item)).hexdigest():
            return None
        if (
            item.get("contract") != OBSERVATION_CONTRACT
            or item.get("scenario_id") != scenario_id
            or item.get("change_id") != change_id
        ):
            return None
        item["checksum"] = checksum
        return item
    except (ValueError, OSError, TypeError):
        return None



def read_reusable_terminal_row(
    root: Path,
    *,
    change_id: str,
    scenario_id: str,
    expected_node: str,
    row_digest: str | None,
    shared_digest: str,
    source_digest: str,
    plan_evidence_id: str,
    row_identity_contract: str = "",
) -> tuple[ScenarioCoverageRowReview, list[ScenarioCoverageGap], str, dict[str, str]] | None:
    """Recover a validated negative LLM-audit review, never a PASS or unknown run.

    The per-row digest permits reuse across changes to other isolated test
    modules. When no per-row digest exists, require the full source digest.
    Shared scope binds model tier, verifier contract and frozen plan identity.
    """
    if not shared_digest or not source_digest or not plan_evidence_id or not expected_node:
        return None
    obs = read_observation(root, change_id=change_id, scenario_id=scenario_id)
    if obs is None:
        return None
    # A legacy multi-row conversation may hold observations but did not
    # establish an independently valid terminal verdict for each row.
    # A source artifact is reusable only after single-row schema validation.
    if (obs.get("source_artifact_digest")
            and obs.get("recovery_review_mode") != "SINGLE_ROW_VALIDATED"):
        return None
    assessment = obs.get("assessment")
    expected_result = {
        "GAP": "FAIL",
        "CONTRACT_CONFLICT": "PAUSE",
        "INSPECTION_BLOCKED": "PAUSE",
    }.get(assessment)
    product_scope = (
        _product_row_identity(row_identity_contract)
        and _product_row_identity(str(obs.get("row_identity_contract") or ""))
    )
    if (
        expected_result is None or obs.get("result") != expected_result
        or (not product_scope and obs.get("plan_evidence_id") != plan_evidence_id)
        or (row_identity_contract == ROW_CONTRACT and not product_scope)
        or (not product_scope and obs.get("shared_digest") != shared_digest)
        or not isinstance(obs.get("origin_execution_id"), str)
        or not obs["origin_execution_id"].strip()
        or not isinstance(obs.get("evidence_id"), str)
        or not obs["evidence_id"].strip()
        or not isinstance(obs.get("source_digest"), str)
        or not obs["source_digest"].strip()
    ):
        return None
    if row_digest is None:
        if obs.get("row_digest") is not None or obs["source_digest"] != source_digest:
            return None
    elif obs.get("row_digest") != row_digest:
        return None
    try:
        row = ScenarioCoverageRowReview.model_validate(obs["row"])
        raw_gaps = obs.get("gaps")
        if not isinstance(raw_gaps, list):
            return None
        gaps = [ScenarioCoverageGap.model_validate(g) for g in raw_gaps]
    except (ValueError, TypeError, KeyError):
        return None
    if (
        row.scenario_id != scenario_id or row.assessment != assessment
        or (row.test_node_id and row.test_node_id != expected_node)
    ):
        return None
    block_reason = str(obs.get("problem") or "").strip()
    if assessment == "GAP":
        if not gaps or any(g.scenario_id != scenario_id for g in gaps):
            return None
    elif gaps or not block_reason:
        return None
    return row, gaps, (block_reason if assessment != "GAP" else ""), {
        "origin_execution_id": obs["origin_execution_id"],
        "evidence_id": obs["evidence_id"],
    }


def save_supported_row(
    root: Path,
    *,
    change_id: str,
    row: ScenarioCoverageRowReview,
    mapping: Mapping[str, str],
    row_digest: str | None,
    plan_evidence_id: str,
    origin_execution_id: str,
    evidence_id: str,
    row_identity_contract: str = "",
) -> bool:
    """Independent proof and identity gate for one atomic PASS receipt."""
    if row.assessment != "SUPPORTED" or not row_digest:
        return False
    if row.test_node_id != mapping.get(row.scenario_id):
        return False
    if review_proof_error(
        root, change_id, row, expected_node=row.test_node_id,
    ) is not None:
        return False
    return best_effort_write_pass(
        root=root, change_id=change_id,
        check=row_check(row.scenario_id),
        contract=ROW_PASS_CONTRACT,
        input_digest=row_digest,
        origin_execution_id=origin_execution_id,
        evidence_id=evidence_id,
        pass_verdict=ROW_PASS_VERDICT,
        result={
            "verdict": ROW_PASS_VERDICT,
            "scenario_id": row.scenario_id,
            "plan_evidence_id": plan_evidence_id,
            "row_identity_contract": row_identity_contract,
            "row": row.model_dump(mode="json"),
        },
    )


def read_supported_row(
    root: Path,
    *,
    change_id: str,
    scenario_id: str,
    mapping: Mapping[str, str],
    row_digest: str | None,
    plan_evidence_id: str,
    row_identity_contract: str = "",
) -> tuple[ScenarioCoverageRowReview, dict[str, str]] | None:
    """A stale or altered row returns MISS; previous execution is not current."""
    if not row_digest or scenario_id not in mapping:
        return None
    receipt = read_pass(
        root,
        change_id=change_id,
        check=row_check(scenario_id),
        contract=ROW_PASS_CONTRACT,
        input_digest=row_digest,
        pass_verdict=ROW_PASS_VERDICT,
    )
    if receipt is None:
        return None
    value = receipt["result"]
    product_scope = (
        _product_row_identity(row_identity_contract)
        and _product_row_identity(str(value.get("row_identity_contract") or ""))
    )
    if (
        value.get("scenario_id") != scenario_id
        or (not product_scope and value.get("plan_evidence_id") != plan_evidence_id)
        or (row_identity_contract == ROW_CONTRACT and not product_scope)
    ):
        return None
    try:
        row = ScenarioCoverageRowReview.model_validate(value["row"])
    except (ValueError, TypeError, KeyError):
        return None
    if (
        row.assessment != "SUPPORTED"
        or row.scenario_id != scenario_id
        or row.test_node_id != mapping[scenario_id]
        or review_proof_error(
            root, change_id, row, expected_node=mapping[scenario_id],
        ) is not None
    ):
        return None
    return row, {
        "origin_execution_id": receipt["origin_execution_id"],
        "evidence_id": receipt["evidence_id"],
    }
