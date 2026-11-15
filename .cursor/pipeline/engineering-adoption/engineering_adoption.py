"""OPSX project engineering adoption workflow (DISCOVER → ADOPTED).

State machine и bounded assess/verify для ``/opsx-adopt``. Пишет только в
``opsx/engineering-adoption/**``, ``opsx/engineering-adoption.yaml`` и
одобренные quality config surfaces (pyright).

Граница агента (см. ``.cursor/commands/opsx-adopt.md``): **не** правит
``src/**``, ``tests/**``, migrations; сбой suite → Owner quarantine или infra
в whitelist, не product/test remediation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import yaml

# Импорт gate из repo root (pipeline tests добавляют parents[3] в sys.path).
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_OPSX_RUNTIME = _REPO_ROOT / "opsx" / "opsx_runtime"
if str(_OPSX_RUNTIME) not in sys.path:
    sys.path.insert(0, str(_OPSX_RUNTIME))

from tools.openspec.opsx_engineering_adoption_gate import (  # noqa: E402
    CONTRACT_ID,
    CONTRACT_REL,
    LOCK_REL,
    QUARANTINE_REL,
    VERIFY_RECEIPT_REL,
    WATCHED_CONFIG_PATHS,
    compute_config_fingerprint,
    compute_contract_content_hash,
    compute_verification_receipt_sha256,
    read_adoption_lock,
    validate_quarantine_record,
    validate_verified_adoption,
)
from tools.openspec.opsx_mutation_preflight import _projectization_allows  # noqa: E402

CONTRACT_DIR_REL = Path("opsx/engineering-adoption")

IMPLEMENTATION_ID = "stroy-engineering-adoption-workflow-v1.0.0"
STATE_REL = Path("opsx/engineering-adoption/state.json")
DISCOVER_REL = Path("opsx/engineering-adoption/discover.json")
ASSESS_REL = Path("opsx/engineering-adoption/assess.json")
POLICY_REL = Path("opsx/engineering-adoption/policy-proposed.json")
PYRIGHT_POLICY_REL = Path("opsx/engineering-adoption/pyright-policy.json")
PYRIGHT_BASELINE_REL = Path("opsx/engineering-adoption/pyright-baseline.json")

TERMINAL_STATES = frozenset({"ADOPTED", "ADOPTED_WITH_QUARANTINE", "FAIL_CLOSED"})
OWNER_APPROVE_PREFIX = "OWNER_POLICY_APPROVED:"

DEFAULT_ASSESS_TIMEOUT_S = 120
DEFAULT_TEST_COLLECT_TIMEOUT_S = 60
DEFAULT_HANGING_TEST_TIMEOUT_S = 30
DEFAULT_VERIFY_PYTEST_TIMEOUT_S = 600


def _adoption_pytest_subprocess_env() -> dict[str, str]:
    """Окружение pytest для VERIFY/ASSESS: disposable PG + CI ingest guard (без cloud-start global allow)."""

    env = os.environ.copy()
    env.setdefault("STROY_ALLOW_DESTRUCTIVE_TEST_DB", "1")
    env.setdefault(
        "TEST_DATABASE_URL",
        "postgresql+asyncpg://stroy_test:test_only_local@127.0.0.1:55432/stroy_test",
    )
    env.setdefault(
        "DATABASE_URL",
        "postgresql+asyncpg://stroy_prod_ci_marker:unused@127.0.0.1:55432/stroy_prod_ci_marker",
    )
    env.setdefault("INGEST_API_KEY", "ci-only-internal-secret-do-not-reuse")
    return env


@dataclass(frozen=True)
class TransitionResult:
    """Результат одного шага adoption."""

    ok: bool
    status: str
    detail: str = ""
    next_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "detail": self.detail,
            "next_action": self.next_action,
            "implementation": IMPLEMENTATION_ID,
        }


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _run_bounded(
    cmd: list[str],
    *,
    cwd: Path,
    timeout_s: float,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Запустить subprocess с детерминированным timeout."""

    resolved_cmd = list(cmd)
    if os.name == "nt" and resolved_cmd and resolved_cmd[0] == "pyright":
        launcher = shutil.which("pyright.cmd")
        if launcher:
            resolved_cmd[0] = launcher

    try:
        proc = subprocess.run(
            resolved_cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
            env=env,
        )
        return {
            "cmd": cmd,
            "returncode": proc.returncode,
            "stdout_tail": (proc.stdout or "")[-4000:],
            "stdout_full": proc.stdout if "--outputjson" in cmd else None,
            "stderr_tail": (proc.stderr or "")[-4000:],
            "timed_out": False,
        }
    except FileNotFoundError:
        return {
            "cmd": cmd,
            "returncode": 127,
            "stdout_tail": "",
            "stderr_tail": "executable not found",
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        def _tail_stream(data: str | bytes | None, limit: int = 2000) -> str:
            if data is None:
                return ""
            if isinstance(data, bytes):
                text = data.decode("utf-8", errors="replace")
            else:
                text = data
            return text[-limit:]

        return {
            "cmd": cmd,
            "returncode": None,
            "stdout_tail": _tail_stream(exc.stdout),
            "stderr_tail": _tail_stream(exc.stderr),
            "timed_out": True,
        }


def load_capabilities(root: Path) -> dict[str, Any]:
    path = root / "opsx/project/capabilities.yaml"
    if not path.is_file():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def discover_stack(root: Path) -> dict[str, Any]:
    """DISCOVER: факты стека из capabilities и репозитория."""

    caps = load_capabilities(root)
    product = caps.get("product") if isinstance(caps.get("product"), dict) else {}
    quality = caps.get("quality") if isinstance(caps.get("quality"), dict) else {}
    pyproject = root / "pyproject.toml"
    has_poetry = pyproject.is_file()
    python_version = None
    if has_poetry:
        text = pyproject.read_text(encoding="utf-8", errors="replace")
        match = re.search(r'python\s*=\s*"([^"]+)"', text)
        python_version = match.group(1) if match else None

    ci_files = []
    workflows = root / ".github/workflows"
    if workflows.is_dir():
        ci_files = sorted(p.name for p in workflows.glob("*.yml"))

    return {
        "schema": "opsx.engineering_adoption.discover",
        "discovered_at": datetime.now(UTC).isoformat(),
        "language": product.get("language"),
        "package_manager": product.get("package_manager"),
        "python_constraint": python_version,
        "source_roots": product.get("source_roots"),
        "test_roots": product.get("test_roots"),
        "quality_capabilities": quality,
        "ci_workflows": ci_files,
        "has_pyproject": has_poetry,
        "has_pyrightconfig": (root / "pyrightconfig.json").is_file(),
    }


def assess_project(
    root: Path,
    *,
    timeout_s: float = DEFAULT_ASSESS_TIMEOUT_S,
) -> dict[str, Any]:
    """ASSESS: bounded checks без изменения product tree."""

    passing: list[str] = []
    failing: list[dict[str, Any]] = []
    hanging: list[dict[str, Any]] = []
    blockers: list[str] = []
    missing_tooling: list[str] = []

    compile_run = _run_bounded(
        [sys.executable, "-m", "compileall", "-q", "src", "tests", "migrations"],
        cwd=root,
        timeout_s=min(timeout_s, 90),
    )
    if compile_run["timed_out"]:
        hanging.append({"check": "compileall", **compile_run})
    elif compile_run["returncode"] == 0:
        passing.append("syntax:compileall")
    else:
        failing.append({"check": "syntax:compileall", **compile_run})

    collect_run = _run_bounded(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=root,
        timeout_s=min(timeout_s, DEFAULT_TEST_COLLECT_TIMEOUT_S),
        env=_adoption_pytest_subprocess_env(),
    )
    if collect_run["timed_out"]:
        hanging.append({"check": "pytest_collect", **collect_run})
    elif collect_run["returncode"] == 0:
        passing.append("tests:pytest_collect")
    else:
        failing.append({"check": "tests:pytest_collect", **collect_run})

    pyright_run = _run_bounded(
        ["pyright", "--version"],
        cwd=root,
        timeout_s=15,
    )
    pyright_available = pyright_run["returncode"] == 0 and not pyright_run["timed_out"]
    if not pyright_available:
        missing_tooling.append("pyright")

    pyright_check: dict[str, Any] | None = None
    if pyright_available:
        pyright_check = _run_bounded(
            ["pyright", "--outputjson"],
            cwd=root,
            timeout_s=timeout_s,
        )
        if pyright_check["timed_out"]:
            hanging.append({"check": "pyright", **pyright_check})
        elif pyright_check["returncode"] == 0:
            passing.append("typecheck:pyright")
        else:
            failing.append({"check": "typecheck:pyright", **pyright_check})

    return {
        "schema": "opsx.engineering_adoption.assess",
        "assessed_at": datetime.now(UTC).isoformat(),
        "passing_checks": passing,
        "failing_checks": failing,
        "hanging_checks": hanging,
        "infrastructure_blockers": blockers,
        "missing_tooling": missing_tooling,
        "legacy_diagnostics": {
            "pyright_available": pyright_available,
            "pyright_check": pyright_check,
        },
    }


def policy_proposal_content_sha256(policy: dict[str, Any]) -> str:
    """SHA-256 канонического policy proposal (без volatile/derived полей).

    ``proposed_at`` и ``policy_revision_sha256`` исключаются: revision — digest
    остального тела, его включение ломает OWNER token binding.
    """

    body = {
        k: v
        for k, v in policy.items()
        if k not in {"proposed_at", "policy_revision_sha256"}
    }
    return _sha256_bytes(json.dumps(body, sort_keys=True).encode("utf-8"))


def verify_owner_policy_approval(root: Path, owner_token: str | None) -> str | None:
    """Вернуть код ошибки или None если Owner approval привязан к policy identity."""

    if not owner_token or not str(owner_token).startswith(OWNER_APPROVE_PREFIX):
        return "OWNER_POLICY_UNAUTHENTICATED"
    policy = _read_json(root / POLICY_REL)
    if policy is None:
        return "OWNER_POLICY_MISSING"
    expected = f"{OWNER_APPROVE_PREFIX}{policy_proposal_content_sha256(policy)}"
    if owner_token != expected:
        return "OWNER_POLICY_BINDING_MISMATCH"
    return None


def propose_policy(discover: dict[str, Any], assess: dict[str, Any]) -> dict[str, Any]:
    """POLICY_PROPOSED: Project Quality Contract draft."""

    language = discover.get("language") or "unknown"
    toolchain: dict[str, Any] = {
        "typechecker": "pyright" if language == "python" else None,
        "test_runner": "pytest" if language == "python" else None,
        "linter_optional": "ruff",
        "ruff_mandatory": False,
        "mypy_mandatory": False,
    }
    pyright_policy = {
        "mode": "ratchet",
        # Strict-on-changed and pyright-strict/mypy gates live under
        # `/opsx-static-check-coverage` + `/opsx-static-check`, not adoption VERIFY.
        "new_and_modified_strict": False,
        "baseline_path": str(PYRIGHT_BASELINE_REL),
        "policy_path": str(PYRIGHT_POLICY_REL),
        "silent_relax_forbidden": True,
    }
    mandatory_checks = ["syntax:compileall", "tests:pytest_active_suite"]
    if toolchain["typechecker"]:
        mandatory_checks.append("typecheck:pyright_ratchet")

    quarantine_candidates = [
        {
            "test_node_id": item.get("check"),
            "reason": "bounded_timeout_exceeded",
            "source": "assess",
        }
        for item in assess.get("hanging_checks", [])
        if isinstance(item, dict)
    ]

    proposal = {
        "schema": "opsx.engineering_adoption.policy_proposed",
        "contract_id": CONTRACT_ID,
        "proposed_at": datetime.now(UTC).isoformat(),
        "toolchain": toolchain,
        "mandatory_checks": mandatory_checks,
        "pyright_policy": pyright_policy,
        "quarantine_candidates": quarantine_candidates,
        "owner_decisions_required": [
            "approve_toolchain",
            "approve_pyright_ratchet",
            "approve_quarantine_entries",
        ],
        "watched_config_paths": list(WATCHED_CONFIG_PATHS),
    }
    proposal["policy_revision_sha256"] = policy_proposal_content_sha256(proposal)
    return proposal


def _default_pyrightconfig() -> dict[str, Any]:
    return {
        "include": ["src", "tests", "tools", ".cursor/pipeline"],
        "exclude": ["**/__pycache__", ".venv", ".venv-ae"],
        "pythonVersion": "3.11",
        "typeCheckingMode": "standard",
        "reportMissingImports": True,
        "reportMissingTypeStubs": False,
    }


def configure_toolchain(root: Path, policy: dict[str, Any]) -> list[str]:
    """CONFIGURE: только approved dependency/config surfaces."""

    written: list[str] = []
    pyright_cfg = root / "pyrightconfig.json"
    if not pyright_cfg.is_file():
        _write_json(pyright_cfg, _default_pyrightconfig())
        written.append("pyrightconfig.json")

    pyright_policy = policy.get("pyright_policy")
    if isinstance(pyright_policy, dict):
        policy_path = root / PYRIGHT_POLICY_REL
        _write_json(policy_path, pyright_policy)
        written.append(str(PYRIGHT_POLICY_REL))

    caps_path = root / "opsx/project/capabilities.yaml"
    if caps_path.is_file():
        caps = yaml.safe_load(caps_path.read_text(encoding="utf-8"))
        if isinstance(caps, dict):
            quality = caps.setdefault("quality", {})
            if isinstance(quality, dict):
                tc = quality.setdefault("typecheck", {})
                if isinstance(tc, dict):
                    tc["status"] = "resolved"
                    tc["command"] = "pyright --outputjson"
                caps_path.write_text(yaml.safe_dump(caps, sort_keys=False), encoding="utf-8")
                written.append("opsx/project/capabilities.yaml")
    return written


def _parse_pyright_diagnostics(stdout: str) -> list[dict[str, Any]] | None:
    """Full JSON only: malformed or truncated output must never mean no errors."""
    try:
        data = json.loads(stdout)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    diags = data.get("generalDiagnostics", data.get("diagnostics"))
    if not isinstance(diags, list) or any(not isinstance(d, dict) for d in diags):
        return None
    return diags


def _checked_pyright_diagnostics(run: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Reject tool failures and incomplete evidence even when JSON parsing succeeds."""
    if run.get("timed_out") or run.get("returncode") not in (0, 1):
        return None
    stdout = run.get("stdout_full")
    if stdout is None:
        stdout = run.get("stdout_tail", "")  # hermetic mock compatibility
    diags = _parse_pyright_diagnostics(stdout)
    if diags is None or (run.get("returncode") == 1 and not diags):
        return None
    return diags


def diagnostic_signature(diag: dict[str, Any]) -> str:
    file = str(diag.get("file", ""))
    line = diag.get("range", {}).get("start", {}).get("line", diag.get("line", 0))
    rule = str(diag.get("rule", diag.get("diag", "")))
    message = str(diag.get("message", ""))[:120]
    raw = f"{file}:{line}:{rule}:{message}"
    return _sha256_bytes(raw.encode("utf-8"))


def capture_pyright_baseline(root: Path, *, timeout_s: float) -> dict[str, Any]:
    run = _run_bounded(["pyright", "--outputjson"], cwd=root, timeout_s=timeout_s)
    diags = _checked_pyright_diagnostics(run)
    if diags is None:
        return {
            "capture_ok": False,
            "reason": "PYRIGHT_OUTPUT_UNTRUSTED",
            "pyright_returncode": run.get("returncode"),
            "timed_out": run.get("timed_out", False),
        }
    signatures = sorted({diagnostic_signature(d) for d in diags})
    baseline = {
        "schema": "opsx.engineering_adoption.pyright_baseline",
        "captured_at": datetime.now(UTC).isoformat(),
        "diagnostic_signatures": signatures,
        "diagnostic_count": len(signatures),
        "pyright_returncode": run.get("returncode"),
        "timed_out": run.get("timed_out", False),
    }
    _write_json(root / PYRIGHT_BASELINE_REL, baseline)
    return baseline


def _git_changed_python_files(root: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(root), "diff", "--name-only", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    files = []
    for line in proc.stdout.splitlines():
        if line.endswith(".py"):
            files.append(line.strip())
    return files


def verify_active_gates(
    root: Path,
    policy: dict[str, Any],
    quarantine: dict[str, Any] | None,
) -> dict[str, Any]:
    """VERIFY: active quality gates (quarantine excluded from active suite)."""

    excluded_nodes = set()
    if quarantine and isinstance(quarantine.get("records"), list):
        for rec in quarantine["records"]:
            if isinstance(rec, dict) and rec.get("test_node_id"):
                excluded_nodes.add(str(rec["test_node_id"]))

    results: dict[str, Any] = {
        "schema": "opsx.engineering_adoption.verify",
        "verified_at": datetime.now(UTC).isoformat(),
        "active_quality_gates": {},
        "quarantined_tests": sorted(excluded_nodes),
        "full_suite_verification_status": "NOT_RUN",
    }

    compile_run = _run_bounded(
        [sys.executable, "-m", "compileall", "-q", "src", "tests", "migrations"],
        cwd=root,
        timeout_s=90,
    )
    results["active_quality_gates"]["syntax"] = compile_run["returncode"] == 0 and not compile_run[
        "timed_out"
    ]

    from workflows.verify.pytest_workflow_argv import (
        build_workflow_pytest_argv,
        default_workflow_pytest_paths,
    )

    pytest_args = build_workflow_pytest_argv(
        root,
        paths=default_workflow_pytest_paths(root),
        python_executable=sys.executable,
    )
    for node in excluded_nodes:
        pytest_args.extend(["--deselect", node])
    pytest_env = _adoption_pytest_subprocess_env()
    test_run = _run_bounded(
        pytest_args,
        cwd=root,
        timeout_s=DEFAULT_VERIFY_PYTEST_TIMEOUT_S,
        env=pytest_env,
    )
    results["active_quality_gates"]["pytest_active_suite"] = (
        test_run["returncode"] == 0 and not test_run["timed_out"]
    )
    results["active_quality_gates"]["pytest_timed_out"] = test_run["timed_out"]

    baseline_path = root / PYRIGHT_BASELINE_REL
    baseline = _read_json(baseline_path) or {"diagnostic_signatures": []}
    pyright_version = _run_bounded(["pyright", "--version"], cwd=root, timeout_s=15)
    pyright_available = pyright_version["returncode"] == 0 and not pyright_version["timed_out"]
    pyright_run = (
        _run_bounded(["pyright", "--outputjson"], cwd=root, timeout_s=DEFAULT_ASSESS_TIMEOUT_S)
        if pyright_available
        else {"timed_out": False, "returncode": 127, "stdout_tail": ""}
    )
    new_sigs: set[str] = set()
    strict_violations: list[str] = []
    if not pyright_available:
        results["active_quality_gates"]["pyright_no_new_standard_errors"] = False
        results["active_quality_gates"]["pyright_strict_modified_clean"] = False
        results["active_quality_gates"]["pyright_missing"] = True
    else:
        diags = _checked_pyright_diagnostics(pyright_run)
        if diags is None:
            results["active_quality_gates"]["pyright_no_new_standard_errors"] = False
            results["active_quality_gates"]["pyright_strict_modified_clean"] = False
            results["active_quality_gates"]["pyright_output_untrusted"] = True
        else:
            current_sigs = {diagnostic_signature(d) for d in diags}
            base_set = set(baseline.get("diagnostic_signatures") or [])
            new_sigs = current_sigs - base_set
            changed = set(_git_changed_python_files(root))
            for diag in diags:
                file = str(diag.get("file", "")).strip()
                if not file:
                    continue
                path = Path(file)
                try:
                    rel = (path if path.is_absolute() else root / path).resolve().relative_to(root.resolve()).as_posix()
                except (ValueError, OSError):
                    continue
                if rel in changed and str(diag.get("severity", "")).lower() in ("error", "8"):
                    strict_violations.append(rel)
            results["active_quality_gates"]["pyright_no_new_standard_errors"] = len(new_sigs) == 0
            results["active_quality_gates"]["pyright_new_signatures"] = sorted(new_sigs)
            results["active_quality_gates"]["pyright_strict_modified_clean"] = len(
                strict_violations
            ) == 0

    existing_strict_gate = root / "tools/pyright/run_local_gate.sh"
    if existing_strict_gate.is_file():
        # Adoption must never replace an established Strict-on-Touch gate with a weaker ratchet.
        if os.name == "nt":
            results["active_quality_gates"]["strict_on_touch"] = False
            results["active_quality_gates"]["strict_on_touch_reason"] = "REQUIRES_LINUX_RUNNER"
        else:
            gate = _run_bounded(["bash", str(existing_strict_gate)], cwd=root, timeout_s=DEFAULT_ASSESS_TIMEOUT_S)
            results["active_quality_gates"]["strict_on_touch"] = (
                gate["returncode"] == 0 and not gate["timed_out"]
            )

    full_run = _run_bounded(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=root,
        timeout_s=DEFAULT_ASSESS_TIMEOUT_S,
        env=pytest_env,
    )
    results["full_suite_verification_status"] = (
        "PASS" if full_run["returncode"] == 0 and not full_run["timed_out"] else "FAIL_OR_TIMEOUT"
    )

    required_gates = ["syntax", "pytest_active_suite", "pyright_no_new_standard_errors"]
    # Informational only (not adoption blockers): strict-on-touch local gate,
    # pyright_strict_modified_clean → `/opsx-static-check-coverage` + static-check.
    results["verify_ok"] = all(results["active_quality_gates"].get(k) is True for k in required_gates)
    return results


def _audit_incremental_quality_coverage(root: Path) -> dict[str, Any]:
    """Read-only IQC V1 block for quality audit (PROPOSED manifest does not fail verify)."""
    iqc_dir = root / ".cursor/pipeline/incremental-quality-coverage"
    if not iqc_dir.is_dir():
        return {"STATUS": "BLOCKED", "REASON": "IQC_MODULE_MISSING"}
    if str(iqc_dir) not in sys.path:
        sys.path.insert(0, str(iqc_dir))
    try:
        from iqc_quality_audit import audit_incremental_quality_coverage  # noqa: WPS433

        return audit_incremental_quality_coverage(root)
    except Exception as exc:  # pragma: no cover
        return {"STATUS": "BLOCKED", "REASON": "IQC_AUDIT_ERROR", "DETAIL": str(exc)}


def build_adoption_contract(
    root: Path,
    policy: dict[str, Any],
    verify: dict[str, Any],
    quarantine: dict[str, Any] | None,
) -> dict[str, Any]:
    contract: dict[str, Any] = {
        "schema": "opsx.engineering_adoption.contract",
        "contract_id": CONTRACT_ID,
        "toolchain": policy.get("toolchain"),
        "mandatory_checks": policy.get("mandatory_checks"),
        "pyright_policy": policy.get("pyright_policy"),
        "watched_config_paths": policy.get("watched_config_paths", list(WATCHED_CONFIG_PATHS)),
        "quarantine_test_count": len((quarantine or {}).get("records") or []),
        "verify_summary": {
            "active_quality_gates": verify.get("active_quality_gates"),
            "full_suite_verification_status": verify.get("full_suite_verification_status"),
        },
        "adopted_at": datetime.now(UTC).isoformat(),
    }
    contract["content_hash"] = compute_contract_content_hash(contract)
    return contract


def write_adoption_lock(
    root: Path,
    contract: dict[str, Any],
    receipt: dict[str, Any],
    *,
    with_quarantine: bool,
) -> None:
    status = "ADOPTED_WITH_QUARANTINE" if with_quarantine else "ADOPTED"
    lock = {
        "status": status,
        "contract_id": CONTRACT_ID,
        "content_hash": contract["content_hash"],
        "verification_receipt_sha256": compute_verification_receipt_sha256(receipt),
        "mutation_commands_enabled": True,
        "completed_by": "opsx-adopt",
        "completed_at": datetime.now(UTC).isoformat(),
    }
    (root / LOCK_REL).parent.mkdir(parents=True, exist_ok=True)
    (root / LOCK_REL).write_text(yaml.safe_dump(lock, sort_keys=False), encoding="utf-8")


def load_state(root: Path) -> dict[str, Any]:
    return _read_json(root / STATE_REL) or {"status": "DISCOVER"}


_ACTION_REQUIRES_STATUS: dict[str, frozenset[str]] = {
    "DISCOVER": frozenset({"DISCOVER"}),
    "ASSESS": frozenset({"ASSESS"}),
    "POLICY_PROPOSED": frozenset({"POLICY_PROPOSED"}),
    "OWNER_POLICY_GATE": frozenset({"OWNER_POLICY_GATE"}),
    "CONFIGURE": frozenset({"CONFIGURE"}),
    "VERIFY": frozenset({"VERIFY"}),
}


def _require_status(state: dict[str, Any], action: str) -> TransitionResult | None:
    current = str(state.get("status", "DISCOVER"))
    allowed = _ACTION_REQUIRES_STATUS.get(action, frozenset())
    if current not in allowed:
        return TransitionResult(
            False,
            "FAIL_CLOSED",
            f"invalid transition: status={current!r} action={action}",
            next_action="/opsx-adopt STATUS",
        )
    return None


def save_state(root: Path, status: str, extra: dict[str, Any] | None = None) -> None:
    body = {
        "schema": "opsx.engineering_adoption.state",
        "status": status,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    if extra:
        body.update(extra)
    _write_json(root / STATE_REL, body)


def transition(
    root: Path,
    action: str,
    *,
    owner_token: str | None = None,
    quarantine_records: list[dict[str, Any]] | None = None,
    assess_timeout_s: float = DEFAULT_ASSESS_TIMEOUT_S,
) -> TransitionResult:
    """Выполнить один шаг state machine."""

    root = root.resolve()
    action_norm = action.strip().upper()
    state = load_state(root)

    if action_norm == "STATUS":
        existing = validate_verified_adoption(root)
        if existing.ok:
            return TransitionResult(True, str(read_adoption_lock(root).get("status", "ADOPTED")))
        return TransitionResult(True, state.get("status", "DISCOVER"))

    if action_norm == "DISCOVER":
        proj = _projectization_allows(root)
        if proj is not None:
            return TransitionResult(
                False,
                "FAIL_CLOSED",
                proj.detail,
                next_action=proj.next_action,
            )
        blocked = _require_status(state, action_norm)
        if blocked is not None:
            return blocked
        disc = discover_stack(root)
        _write_json(root / DISCOVER_REL, disc)
        save_state(root, "ASSESS", {"discover_sha256": _sha256_bytes(json.dumps(disc).encode())})
        return TransitionResult(True, "ASSESS", "discover.json written")

    if action_norm == "ASSESS":
        blocked = _require_status(state, action_norm)
        if blocked is not None:
            return blocked
        assess = assess_project(root, timeout_s=assess_timeout_s)
        _write_json(root / ASSESS_REL, assess)
        save_state(root, "POLICY_PROPOSED")
        return TransitionResult(True, "POLICY_PROPOSED")

    if action_norm == "POLICY_PROPOSED":
        blocked = _require_status(state, action_norm)
        if blocked is not None:
            return blocked
        disc = _read_json(root / DISCOVER_REL) or discover_stack(root)
        assess = _read_json(root / ASSESS_REL) or assess_project(root)
        policy = propose_policy(disc, assess)
        _write_json(root / POLICY_REL, policy)
        save_state(root, "OWNER_POLICY_GATE")
        return TransitionResult(True, "OWNER_POLICY_GATE", "policy-proposed.json written")

    if action_norm == "OWNER_POLICY_GATE":
        blocked = _require_status(state, action_norm)
        if blocked is not None:
            return blocked
        approval_err = verify_owner_policy_approval(root, owner_token)
        if approval_err:
            policy = _read_json(root / POLICY_REL) or {}
            revision = policy.get("policy_revision_sha256", "")
            return TransitionResult(
                False,
                "OWNER_POLICY_GATE",
                approval_err,
                next_action=f"/opsx-adopt OWNER_POLICY_GATE {OWNER_APPROVE_PREFIX}{revision}",
            )
        save_state(root, "CONFIGURE", {"owner_approved_at": datetime.now(UTC).isoformat()})
        return TransitionResult(True, "CONFIGURE")

    if action_norm == "CONFIGURE":
        blocked = _require_status(state, action_norm)
        if blocked is not None:
            return blocked
        policy = _read_json(root / POLICY_REL)
        if policy is None:
            return TransitionResult(False, "FAIL_CLOSED", "missing policy-proposed.json")
        written = configure_toolchain(root, policy)
        baseline = capture_pyright_baseline(root, timeout_s=assess_timeout_s)
        if baseline.get("capture_ok") is False:
            return TransitionResult(
                False, "CONFIGURE", "PYRIGHT_BASELINE_UNTRUSTED",
                next_action="restore toolchain and rerun /opsx-adopt CONFIGURE",
            )
        save_state(root, "VERIFY", {"configured": written})
        return TransitionResult(True, "VERIFY", detail=",".join(written))

    if action_norm == "VERIFY":
        blocked = _require_status(state, action_norm)
        if blocked is not None:
            return blocked
        policy = _read_json(root / POLICY_REL)
        if policy is None:
            return TransitionResult(False, "FAIL_CLOSED", "missing policy")
        assess_doc = _read_json(root / ASSESS_REL) or {}
        discover_doc = _read_json(root / DISCOVER_REL) or {}
        if discover_doc.get("language") == "python":
            pyright_run = _run_bounded(["pyright", "--version"], cwd=root, timeout_s=15)
            pyright_ok = pyright_run["returncode"] == 0 and not pyright_run["timed_out"]
            if not pyright_ok:
                return TransitionResult(
                    False,
                    "VERIFY",
                    "MANDATORY_TOOL_MISSING: pyright",
                    next_action="/opsx-adopt CONFIGURE",
                )
        quarantine_body: dict[str, Any] | None = None
        if quarantine_records:
            now = datetime.now(UTC)
            for record in quarantine_records:
                if not isinstance(record, dict):
                    return TransitionResult(False, "FAIL_CLOSED", "invalid quarantine record")
                q_err = validate_quarantine_record(record, now=now)
                if q_err:
                    return TransitionResult(False, "FAIL_CLOSED", q_err)
            quarantine_body = {
                "schema": "opsx.engineering_adoption.quarantine",
                "records": quarantine_records,
            }
            _write_json(root / QUARANTINE_REL, quarantine_body)
        elif (root / QUARANTINE_REL).is_file():
            quarantine_body = _read_json(root / QUARANTINE_REL)

        verify = verify_active_gates(root, policy, quarantine_body)
        _write_json(root / CONTRACT_DIR_REL / "verify-result.json", verify)
        if not verify.get("verify_ok"):
            save_state(root, "VERIFY")
            return TransitionResult(
                False,
                "VERIFY",
                "active quality gates failed",
                next_action="/opsx-adopt ASSESS",
            )

        contract = build_adoption_contract(root, policy, verify, quarantine_body)
        _write_json(root / CONTRACT_REL, contract)
        receipt = {
            "schema": "opsx.engineering_adoption.verification_receipt",
            "contract_content_hash": contract["content_hash"],
            "config_fingerprint": compute_config_fingerprint(root, contract),
            "verified_at": verify.get("verified_at"),
            "verify_ok": True,
        }
        _write_json(root / VERIFY_RECEIPT_REL, receipt)
        with_q = bool(quarantine_body and quarantine_body.get("records"))
        write_adoption_lock(root, contract, receipt, with_quarantine=with_q)
        final = "ADOPTED_WITH_QUARANTINE" if with_q else "ADOPTED"
        save_state(root, final)
        return TransitionResult(True, final)

    if action_norm == "RUN_ALL":
        for step in (
            "DISCOVER",
            "ASSESS",
            "POLICY_PROPOSED",
            "OWNER_POLICY_GATE",
            "CONFIGURE",
            "VERIFY",
        ):
            token = owner_token if step == "OWNER_POLICY_GATE" else None
            result = transition(root, step, owner_token=token, quarantine_records=quarantine_records)
            if not result.ok:
                return result
        return TransitionResult(True, load_state(root).get("status", "ADOPTED"))

    return TransitionResult(False, "FAIL_CLOSED", f"unknown action {action!r}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OPSX engineering adoption workflow.")
    parser.add_argument("action", help="DISCOVER|ASSESS|...|RUN_ALL|STATUS")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--owner-token", default=None)
    parser.add_argument("--quarantine-json", default=None, help="Path to quarantine records JSON")
    parser.add_argument("--assess-timeout-s", type=float, default=DEFAULT_ASSESS_TIMEOUT_S)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = (args.root or Path.cwd()).resolve()
    records = None
    if args.quarantine_json:
        data = _read_json(Path(args.quarantine_json))
        if data and isinstance(data.get("records"), list):
            records = data["records"]
    result = transition(
        root,
        args.action,
        owner_token=args.owner_token,
        quarantine_records=records,
        assess_timeout_s=args.assess_timeout_s,
    )
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
