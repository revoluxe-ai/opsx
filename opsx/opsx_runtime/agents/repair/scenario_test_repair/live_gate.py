"""Fail-closed gate for an opt-in *real* SDK repair acceptance test.

An actual Verify GraphState is the only input. Recorded historical chat/fixtures
are NEVER promoted into execution authority.
"""
from __future__ import annotations

from opsx.project_context import resource_path

import json
import os
from pathlib import Path
from typing import Any

from agents.repair.scenario_test_repair.agent import validate_input
from agents.verify.scenario_coverage_verify.agent import (
    _audit_completeness_error, _audit_proof_error,
)
from agents.verify.scenario_coverage_verify.evidence_contract import (
    input_snapshot, trace_mappings,
)
from agents.verify.scenario_coverage_verify.input_pack import (
    load_scenario_coverage_plan_bundle,
)
from workflows.implementation.node_subprocess import run_subprocess_utf8
from agents.verify.scenario_coverage_verify.schemas import ScenarioCoverageVerifyAnswer
from pydantic import ValidationError


class LiveGateBlocked(ValueError):
    """No mutating SDK call may occur when this gate rejects the input."""


def load_current_verify_state(
    state_json_path: str | Path, *, repository_path: str | Path,
) -> dict[str, Any]:
    """Load a real serialized Verify GraphState without fabricating fields."""
    path = Path(state_json_path).resolve()
    repo = Path(repository_path).resolve()
    if not path.is_file():
        raise LiveGateBlocked("LIVE_STATE_MISSING: explicit current GraphState JSON required")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise LiveGateBlocked(f"LIVE_STATE_INVALID: {type(exc).__name__}: {exc}") from exc
    if not isinstance(data, dict):
        raise LiveGateBlocked("LIVE_STATE_INVALID: expected full Verify GraphState object")
    # A LangGraph StateSnapshot is often exported as {"values": GraphState}.
    # Unwrap only that native envelope, without reconstructing any fields.
    if "repository_path" not in data and isinstance(data.get("values"), dict):
        data = data["values"]
    if Path(str(data.get("repository_path") or "")).resolve() != repo:
        raise LiveGateBlocked("LIVE_REPOSITORY_MISMATCH: re-audit in isolated target")
    if Path(str(data.get("workspace") or "")).resolve() != repo:
        raise LiveGateBlocked("LIVE_WORKSPACE_MISMATCH: state is from another workspace")
    if not isinstance(data.get("answer"), dict):
        raise LiveGateBlocked("LIVE_IMPL_CONTEXT_MISSING: actual ImplVerify answer required")
    if not isinstance(data.get("scenario_coverage_answer"), dict):
        raise LiveGateBlocked("LIVE_AUDIT_CONTEXT_MISSING: actual scenario audit required")
    if not isinstance(data.get("model"), dict) or not data["model"].get("id"):
        raise LiveGateBlocked("LIVE_MODEL_UNBOUND: preserve actual model/tier from run")
    audit = data["scenario_coverage_answer"]
    # Legacy conversation.json has no source_digest. It is diagnostic input,
    # not a supported live authority; never synthesize a digest for it.
    if not audit.get("source_digest") or not data.get("scenario_coverage_source_snapshot"):
        raise LiveGateBlocked("LIVE_EVIDENCE_UNBOUND: no historical digest rebinding")
    # An old answer may be rebound to the latest digest by a test fixture.
    # Require the entire modern Pydantic contract and all 1:1 source proofs.
    try:
        parsed = ScenarioCoverageVerifyAnswer.model_validate(audit)
        change_id = str(data.get("change_id") or "")
        bundle = load_scenario_coverage_plan_bundle(change_id, repo_root=repo)
        completeness = _audit_completeness_error(parsed, bundle)
        proof_error = _audit_proof_error(
            parsed,
            repo_root=repo,
            change_id=change_id,
            source_snapshot=data["scenario_coverage_source_snapshot"],
            mappings=trace_mappings(repo, change_id),
        )
    except (OSError, ValueError, ValidationError) as exc:
        raise LiveGateBlocked(
            f"LIVE_AUDIT_CONTRACT_INVALID: {type(exc).__name__}: {exc}"
        ) from exc
    if completeness or proof_error:
        raise LiveGateBlocked(
            f"LIVE_AUDIT_NOT_PROVEN: {completeness or proof_error}"
        )
    if not data.get("scenario_coverage_run_id") or not data.get("scenario_coverage_agent_id"):
        raise LiveGateBlocked("LIVE_AUDIT_RUN_UNBOUND: actual SDK run receipt required")
    return data


def check_os_sdk_isolation(
    repo: Path, state: dict[str, Any], proof_json_path: str | Path,
) -> None:
    """Require source-bound OS/SDK isolation evidence AND local read-only mounts.

    External proof is attestation input from a separate authorised execution
    contour. The local checks independently verify read-only/writable mounts.
    They do not by themselves prove network or SDK tool confinement.
    """
    if os.name != "posix" or not Path("/proc/self/mountinfo").is_file():
        raise LiveGateBlocked("LIVE_OS_GATE_UNSUPPORTED: Linux mount proof required")
    repo = repo.resolve()
    proof = Path(proof_json_path).resolve()
    if not proof.is_file():
        raise LiveGateBlocked("LIVE_ISOLATION_PROOF_MISSING")
    try:
        obj = json.loads(proof.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LiveGateBlocked(f"LIVE_ISOLATION_PROOF_INVALID: {exc}") from exc
    if not isinstance(obj, dict) or obj.get("contract") != "opsx_repair_isolation_v1":
        raise LiveGateBlocked("LIVE_ISOLATION_PROOF_INVALID: contract mismatch")
    expected = {
        "repository_path": str(repo),
        "execution_id": str(state["run_uuid"]),
        "source_digest": str(state["scenario_coverage_answer"]["source_digest"]),
        "model_id": str(state["model"]["id"]),
        "allowed_write_root": "tests/",
    }
    for key, value in expected.items():
        if obj.get(key) != value:
            raise LiveGateBlocked(f"LIVE_ISOLATION_PROOF_STALE: {key}")
    if not obj.get("sdk_write_denial_verified") or not obj.get("os_write_denial_verified"):
        raise LiveGateBlocked("LIVE_SDK_OS_WRITE_ISOLATION_UNPROVEN")
    probes = obj.get("probe_receipts")
    if not isinstance(probes, list):
        raise LiveGateBlocked("LIVE_ISOLATION_PROBE_EVIDENCE_MISSING")
    recorded_layers: set[str] = set()
    for entry in probes:
        if not isinstance(entry, dict):
            continue
        layer = entry.get("layer")
        if layer not in ("os", "sdk"):
            continue
        if (
            entry.get("blocked") is True
            and entry.get("source_digest_before") == expected["source_digest"]
            and entry.get("source_digest_after") == expected["source_digest"]
            and entry.get("execution_id") == expected["execution_id"]
            and isinstance(entry.get("attempted_path"), str)
            and entry["attempted_path"].startswith(("src/", "openspec/"))
        ):
            recorded_layers.add(layer)
    if recorded_layers != {"os", "sdk"}:
        raise LiveGateBlocked("LIVE_ISOLATION_PROBE_EVIDENCE_MISSING: OS+SDK denial proofs required")

    # A read-only git/worktree root with a separate RW tests bind mount.
    # This excludes many but not all possible SDK-origin writes.
    if not repo.is_dir() or not (resource_path(repo, "tests")).is_dir():
        raise LiveGateBlocked("LIVE_TEST_ROOT_MISSING")
    read_only = bool(os.statvfs(repo).f_flag & os.ST_RDONLY)
    test_writable = not bool(os.statvfs(resource_path(repo, "tests")).f_flag & os.ST_RDONLY)
    if not read_only or not test_writable:
        raise LiveGateBlocked("LIVE_MOUNT_ISOLATION_UNPROVEN: repo RO + tests RW required")
    for rel in ("src", "openspec", "opsx"):
        target = resource_path(repo, rel)
        if target.exists() and not (os.statvfs(target).f_flag & os.ST_RDONLY):
            raise LiveGateBlocked(f"LIVE_MOUNT_ISOLATION_UNPROVEN: {rel} writable")


def prepare_live_state(
    state_json_path: str | Path,
    *,
    repository_path: str | Path,
    isolation_proof_path: str | Path,
) -> dict[str, Any]:
    """Explicit opt-in boundary. Does not mint fake model, gaps or revision."""
    repo = Path(repository_path).resolve()
    state = load_current_verify_state(state_json_path, repository_path=repo)
    # The user explicitly opted in to a live repair by selecting the test.
    # This boolean alone is NOT an isolation attestation.
    state["scenario_test_repair_authorized"] = True
    state["isolated_workspace"] = True
    problem = validate_input(state, repo_root=repo, change_id=str(state["change_id"]))
    if problem:
        raise LiveGateBlocked(f"LIVE_INPUT_BLOCKED: {problem}")
    check_os_sdk_isolation(repo, state, isolation_proof_path)
    git = run_subprocess_utf8(
        ["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
        timeout=10,
    )
    if git.returncode or Path(git.stdout.strip()).resolve() != repo:
        raise LiveGateBlocked("LIVE_GIT_ROOT_INVALID")
    # Preserve model selection, audit ledger, trace matrix and ImplVerify
    # answer byte-for-byte. Only ephemeral execution-authority flags differ.
    if (
        state["scenario_coverage_answer"]["source_digest"]
        != input_snapshot(repo, str(state["change_id"]))["source_digest"]
    ):
        raise LiveGateBlocked("LIVE_SOURCE_CHANGED_DURING_PREFLIGHT")
    return state
