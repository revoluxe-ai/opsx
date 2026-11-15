"""Read-only, proof-bound compatibility for pre-isolation scenario observations.

No model calls, no rewriting legacy evidence. A hit requires the original
complete v2 product snapshot, exact row digest under a known immutable historic
reviewer build, identical semantic validation, and a finished row's provider
metadata. Other revisions and ambiguous provenance remain cache misses.
"""
from __future__ import annotations

from opsx.project_context import resource_path

import ast
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Mapping

from agents.verify.scenario_coverage_verify import evidence_contract as ev
from agents.verify.scenario_coverage_verify.delta_rows import impact_scope_v1
from agents.verify.scenario_coverage_verify.row_receipts import (
    read_observation, read_supported_row, row_key,
)
from agents.verify.scenario_coverage_verify.schemas import (
    ScenarioCoverageGap, ScenarioCoverageRowReview,
)

# Reviewed pre/post receipt-scheduler revisions. No moving branch aliases.
_HISTORICAL_ANCHORS = (
    "e92bf7d039953d81d2e12b3e351c75a114c46379",
    "4ae68138bf031e123534aaf9141a2aaf2db9878c",
)
_OLD_REVIEW_FUNCTIONS = {
    "opsx/opsx_runtime/agents/verify/scenario_coverage_verify/evidence_contract.py": (
        "spec_clauses", "_test_function_lines", "review_proof_error", "trace_mappings",
    ),
    "opsx/opsx_runtime/agents/verify/scenario_coverage_verify/row_single.py": (
        "verify_single_answer",
    ),
}
_OLD_REVIEW_CONSTANTS = ("_SCENARIO_HEADING", "_CLAUSE")
_UNMOVED_REVIEW_PATHS = (
    "opsx/opsx_runtime/agents/verify/scenario_coverage_verify/semantic_validation.py",
    "opsx/opsx_runtime/agents/verify/scenario_coverage_verify/input_pack.py",
    "opsx/opsx_runtime/agents/verify/scenario_coverage_verify/prompt/get_prompt.py",
    "opsx/opsx_runtime/agents/verify/scenario_coverage_verify/schemas.py",
    "opsx/opsx_runtime/agents/response_schema.py",
    "opsx/opsx_runtime/agents/abc_agent.py",
    "opsx/opsx_runtime/agents/utils/response.py",
)


def _git_bytes(root: Path, anchor: str, rel: str) -> bytes:
    if ".." in Path(rel).parts or not rel.startswith("opsx/"):
        raise ValueError("LEGACY_REVIEW_PATH_UNSAFE")
    proc = subprocess.run(
        ["git", "show", f"{anchor}:{rel}"],
        cwd=root, capture_output=True, check=False, timeout=8,
    )
    if proc.returncode or not proc.stdout:
        raise ValueError(f"LEGACY_REVIEW_BASELINE_UNAVAILABLE: {rel}")
    return proc.stdout


def _definitions(src: bytes) -> dict[str, Any]:
    parsed = ast.parse(src.decode("utf-8"))
    out: dict[str, Any] = {}
    for node in parsed.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[node.name] = ast.dump(node, include_attributes=False)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            out[node.targets[0].id] = ast.dump(node.value, include_attributes=False)
    return out


def _old_authority_digest(root: Path, anchor: str, model: Mapping[str, Any]) -> str:
    """Compute exact legacy verifier digest, not a guessed equivalent digest."""
    old_contract = _git_bytes(
        root, anchor,
        "opsx/opsx_runtime/agents/verify/scenario_coverage_verify/evidence_contract.py",
    )
    tree = ast.parse(old_contract.decode("utf-8"))
    configured: tuple[str, ...] | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_SCENARIO_VERIFIER_FILES"
            for t in node.targets
        ):
            configured = ast.literal_eval(node.value)
    if not configured or not isinstance(configured, tuple):
        raise ValueError("LEGACY_POLICY_INVENTORY_UNBOUND")
    if len(set(configured)) != len(configured) or any(
        not isinstance(rel, str) or not rel.startswith("opsx/") or ".." in Path(rel).parts
        for rel in configured
    ):
        raise ValueError("LEGACY_POLICY_INVENTORY_INVALID")
    h = hashlib.sha256(b"opsx-check-inputs-v1\0")
    for rel in sorted(configured):
        h.update(rel.encode("utf-8") + b"\0")
        h.update(_git_bytes(root, anchor, rel))
        h.update(b"\0")
    payload = json.dumps(
        {"semantic_policy": h.hexdigest(), "model": dict(model)},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _review_semantics_same(root: Path, anchor: str) -> bool:
    current = _definitions((
        resource_path(root, "opsx/opsx_runtime/agents/verify/scenario_coverage_verify/review_semantics.py")
    ).read_bytes())
    for rel, functions in _OLD_REVIEW_FUNCTIONS.items():
        old = _definitions(_git_bytes(root, anchor, rel))
        if any(old.get(name) != current.get(name) for name in functions):
            return False
        if rel.endswith("evidence_contract.py") and any(
            old.get(name) != current.get(name) for name in _OLD_REVIEW_CONSTANTS
        ):
            return False
    for rel in _UNMOVED_REVIEW_PATHS:
        current_path = resource_path(root, rel)
        if not current_path.is_file() or current_path.read_bytes() != _git_bytes(root, anchor, rel):
            return False
    return True


def _verified_meta(
    root: Path, change_id: str, scenario_id: str, observation: Mapping[str, Any], model_id: str,
) -> bool:
    execution = observation.get("origin_execution_id")
    if not isinstance(execution, str) or not execution or not execution.replace("-", "").replace("_", "").isalnum():
        return False
    parent = (
        resource_path(root, "opsx/runs/dialogs", change_id, execution, "scenario_coverage_verify-" + row_key(scenario_id))
    )
    path = parent / "meta.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 32768:
        return False
    try:
        item = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError, UnicodeError):
        return False
    return (
        item.get("sdk_status") == "finished"
        and item.get("run_uuid") == execution
        and item.get("change_id") == change_id
        and item.get("model_id") == model_id
        and item.get("agent") == parent.name
        and isinstance(item.get("sdk_run_id"), str)
        and bool(item["sdk_run_id"])
        and isinstance(item.get("agent_id"), str)
        and bool(item["agent_id"])
        and observation.get("evidence_id") == item["sdk_run_id"] + ":" + item["agent_id"]
    )


def prepare_legacy_reuse(
    root: Path,
    *,
    change_id: str,
    mapping: Mapping[str, str],
    plan_id: str,
    model: Mapping[str, Any],
) -> tuple[str, dict[str, dict[str, str | None]]] | None:
    """Bounded preflight of v2 snapshot and immutable reviewer baselines."""
    # Old metadata does not record options; never infer non-default tier/options.
    if not isinstance(model, Mapping) or not isinstance(model.get("id"), str):
        return None
    if model.get("options", []) != []:
        return None
    try:
        legacy_paths = ev._legacy_paths(root, resource_path(root, "openspec/changes", change_id))
        source = ev.legacy_source_digest(root, change_id)
        scopes = {}
        for anchor in _HISTORICAL_ANCHORS:
            if not _review_semantics_same(root, anchor):
                continue
            policy = _old_authority_digest(root, anchor, model)
            scope = impact_scope_v1(
                root, legacy_paths, mapping,
                authority={
                    "model": dict(model), "plan_evidence_id": plan_id,
                    "verifier_contract_digest": policy,
                },
            )
            scopes[anchor] = scope["row_digests"]
        return (source, scopes) if scopes else None
    except (ValueError, TypeError, OSError, subprocess.TimeoutExpired, UnicodeError):
        return None


def read_legacy_terminal(
    root: Path,
    *,
    change_id: str,
    scenario_id: str,
    mapping: Mapping[str, str],
    plan_id: str,
    model: Mapping[str, Any],
    prepared: tuple[str, dict[str, dict[str, str | None]]],
) -> tuple[ScenarioCoverageRowReview, list[ScenarioCoverageGap], str, dict[str, str]] | None:
    """Re-attest one historical result without invoking the model."""
    source, scoped_digests = prepared
    obs = read_observation(root, change_id=change_id, scenario_id=scenario_id)
    if (
        obs is None or obs.get("source_digest") != source
        or obs.get("plan_evidence_id") != plan_id
        or not _verified_meta(root, change_id, scenario_id, obs, model["id"])
        or not any(digests.get(scenario_id) == obs.get("row_digest") for digests in scoped_digests.values())
    ):
        return None
    try:
        row = ScenarioCoverageRowReview.model_validate(obs["row"])
        gaps = [ScenarioCoverageGap.model_validate(g) for g in obs["gaps"]]
    except (ValueError, TypeError, KeyError):
        return None
    if (
        row.scenario_id != scenario_id
        or (row.test_node_id and row.test_node_id != mapping[scenario_id])
        or (row.assessment == "SUPPORTED" and row.test_node_id != mapping[scenario_id])
    ):
        return None
    result = {"SUPPORTED": "PASS", "GAP": "FAIL",
              "INSPECTION_BLOCKED": "PAUSE", "CONTRACT_CONFLICT": "PAUSE"}.get(row.assessment)
    if obs.get("assessment") != row.assessment or obs.get("result") != result:
        return None
    origin = {
        "origin_execution_id": obs["origin_execution_id"],
        "evidence_id": obs["evidence_id"],
    }
    if row.assessment == "SUPPORTED":
        if gaps or ev.review_proof_error(
            root, change_id, row, expected_node=mapping[scenario_id],
        ) is not None:
            return None
        passed = read_supported_row(
            root, change_id=change_id, scenario_id=scenario_id, mapping=mapping,
            row_digest=obs.get("row_digest"), plan_evidence_id=plan_id,
        )
        if passed is None or passed[0].model_dump(mode="json") != row.model_dump(mode="json"):
            return None
    elif row.assessment == "GAP":
        if not gaps or any(g.scenario_id != scenario_id for g in gaps):
            return None
    else:
        if gaps or not str(obs.get("problem") or "").strip():
            return None
    return row, gaps, str(obs.get("problem") or "") if row.assessment != "GAP" else "", origin
