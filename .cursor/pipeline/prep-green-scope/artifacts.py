"""Durable opsx/prep-green-scope artifacts (schema v2 + STALE detection)."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from contract import (
    COMMAND_CONTRACT_ID,
    COMMAND_CONTRACT_VERSION,
    IMPLEMENTATION_ID,
    PLAN_SCHEMA,
    PROBE_SCHEMA,
    STALE_PLAN_FIELDS,
    STALE_PROBE_GATE_NOTES,
)
from disable_plan import DisablePlan, ProbeGate, resolve_destructive_policy
from full_check import FullStaticCheckResult

_OPSX = Path("opsx/prep-green-scope")
PROBE_REL = _OPSX / "probe-result.json"
PLAN_REL = _OPSX / "plan.json"


def _handoff_module():
    repo = Path(__file__).resolve().parents[3]
    contracts = repo / ".cursor" / "static-check" / "contracts"
    if str(contracts) not in sys.path:
        sys.path.insert(0, str(contracts))
    import prep_green_full_handoff as mod  # noqa: WPS433

    return mod


def artifact_is_stale(
    data: dict[str, Any],
    *,
    expected_static_check_identity: str | None = None,
    expected_static_check_config_identity: str | None = None,
    expected_input_snapshot_identity: str | None = None,
    expected_probe_execution_identity: str | None = None,
    expected_execution_id: str | None = None,
) -> tuple[bool, list[str]]:
    """Несовместимый artifact не управляет GO/APPLY."""

    reasons: list[str] = []
    config_expected = expected_static_check_config_identity or expected_static_check_identity
    schema = str(data.get("schema") or "")
    if not schema.endswith(".v2"):
        reasons.append(f"schema={schema!r} not v2")
    if data.get("command_contract_version") != COMMAND_CONTRACT_VERSION:
        reasons.append("command_contract_version mismatch")
    sc_id = str(
        data.get("static_check_config_identity") or data.get("static_check_identity") or ""
    )
    if config_expected and sc_id != config_expected:
        reasons.append("static_check_config_identity mismatch")
    in_id = str(data.get("input_snapshot_identity") or "")
    if expected_input_snapshot_identity and in_id != expected_input_snapshot_identity:
        reasons.append("input_snapshot_identity mismatch")
    pe_id = str(data.get("probe_execution_identity") or "")
    if expected_probe_execution_identity and pe_id != expected_probe_execution_identity:
        reasons.append("probe_execution_identity mismatch")
    if not sc_id and config_expected:
        reasons.append("static_check_config_identity missing")
    if not in_id and expected_input_snapshot_identity:
        reasons.append("input_snapshot_identity missing")
    ex_id = str(data.get("execution_id") or "")
    if expected_execution_id and ex_id != expected_execution_id:
        reasons.append("execution_id mismatch")
    if not ex_id and expected_execution_id:
        reasons.append("execution_id missing")
    will = data.get("will_apply") or {}
    if isinstance(will, dict):
        for key in STALE_PLAN_FIELDS:
            if key in will:
                reasons.append(f"stale field {key}")
    if data.get("mypy_ignore_errors_global"):
        reasons.append("mypy_ignore_errors_global")
    if data.get("pytest_deselect_count"):
        reasons.append("pytest_deselect_count")
    note = str(data.get("gate_mapping_note") or "")
    for frag in STALE_PROBE_GATE_NOTES:
        if frag in note:
            reasons.append(f"gate_mapping_note contains {frag!r}")
    for n in data.get("notes") or []:
        for frag in STALE_PROBE_GATE_NOTES:
            if frag in str(n):
                reasons.append(f"notes contain {frag!r}")
    return (len(reasons) > 0, reasons)


def write_probe_and_plan(
    root: Path,
    *,
    full: FullStaticCheckResult,
    plan: DisablePlan,
    capabilities: dict[str, Any],
    env_class: str = "",
) -> tuple[Path, Path]:
    """Persist probe + plan after STAGE1 (compatible with ARTIFACT_SCHEMA.md)."""

    pgfh = _handoff_module()
    config_id = (
        full.static_check_config_identity
        or full.static_check_identity
        or pgfh.compute_static_check_config_identity(root)
    )
    input_id = full.input_snapshot_identity or pgfh.compute_input_snapshot_identity(
        full.fingerprint
    )
    dpol = plan.destructive_policy.value
    pe_id = full.probe_execution_identity or pgfh.compute_probe_execution_identity(
        execution_id=full.execution_id or "legacy-no-exec-id",
        static_check_config_identity=config_id,
        input_snapshot_identity=input_id,
        destructive_policy=dpol,
        env_class=env_class,
    )
    now = datetime.now(UTC).isoformat()
    exec_id = full.execution_id or "legacy-no-exec-id"
    probe: dict[str, Any] = {
        "schema": PROBE_SCHEMA,
        "command_contract_id": COMMAND_CONTRACT_ID,
        "command_contract_version": COMMAND_CONTRACT_VERSION,
        "execution_id": exec_id,
        "static_check_identity": config_id,
        "static_check_config_identity": config_id,
        "input_snapshot_identity": input_id,
        "probe_execution_identity": pe_id,
        "implementation": IMPLEMENTATION_ID,
        "probed_at": now,
        "probe_source": "/opsx-static-check FULL",
        "probe_execution_identity_detail": {
            "full_fingerprint": full.fingerprint,
            "full_status": full.status,
            "destructive_policy": dpol,
            "env_class": env_class,
        },
        "destructive_policy": dpol,
        "probe_gate": plan.probe_gate.value,
        "gate_reasons": list(plan.gate_reasons),
        "pyright_ignore_candidates": list(plan.pyright_files),
        "mypy_module_candidates": list(plan.mypy_modules),
        "pytest_failures": list(plan.pytest_nodes),
        "pytest_failure_count": len(plan.pytest_nodes),
        "gate_mapping_note": (
            "pyright→pyrightconfig_strict.json ignore; "
            "mypy→[[tool.mypy.overrides]] per module; "
            "pytest→@pytest.mark.skip(reason=prep-green-scope: …); "
            "probe scope = canonical FULL (all applicable pytest waves)"
        ),
    }
    plan_doc: dict[str, Any] = {
        "schema": PLAN_SCHEMA,
        "command_contract_id": COMMAND_CONTRACT_ID,
        "command_contract_version": COMMAND_CONTRACT_VERSION,
        "execution_id": exec_id,
        "static_check_identity": config_id,
        "static_check_config_identity": config_id,
        "input_snapshot_identity": input_id,
        "probe_execution_identity": pe_id,
        "implementation": IMPLEMENTATION_ID,
        "stage": 1,
        "status": plan.probe_gate.value,
        "planned_at": now,
        "probe_probed_at": now,
        "probe_source": "/opsx-static-check FULL",
        "execute_on_go": {"mode": "agent_apply", "requires_probe": True},
        "will_apply": {
            "pyright_ignore_files": len(plan.pyright_files),
            "mypy_module_override_count": len(plan.mypy_modules),
            "pytest_source_skip_count": len(plan.pytest_nodes),
        },
        "disable_plan": {
            "pyright_files": plan.pyright_files,
            "mypy_modules": plan.mypy_modules,
            "pytest_nodes": plan.pytest_nodes,
        },
        "completion_contract": {
            "terminal_verdict": "PREP_GREEN_SCOPE_READY",
            "requires_full_static_check_pass": True,
            "convergence_loop": True,
        },
    }
    if plan.probe_gate is not ProbeGate.AWAITING_GO:
        plan_doc["go_forbidden"] = True
    out_dir = root / _OPSX
    out_dir.mkdir(parents=True, exist_ok=True)
    probe_path = root / PROBE_REL
    plan_path = root / PLAN_REL
    probe_path.write_text(json.dumps(probe, indent=2) + "\n", encoding="utf-8")
    plan_path.write_text(json.dumps(plan_doc, indent=2) + "\n", encoding="utf-8")
    return probe_path, plan_path


def load_artifact(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}
