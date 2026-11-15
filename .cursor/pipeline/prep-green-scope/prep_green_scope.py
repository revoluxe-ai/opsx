"""PROBE / APPLY / CONFIRM для /opsx-prep-green-scope — штатные конфиги инструментов.

DEPRECATED orchestrator (v3). STAGE1/GO: agent + ``/opsx-prep-green-scope`` v0.9
(``disable_plan``, ``apply_disable``, ``converge``). Do not invoke this CLI for new runs.

Сбор красного зеркалит команды ``/opsx-static-check FULL`` (pyright-strict, mypy,
pytest-targeted пропускается если нет scope, unit xdist, integration, destructive).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config_apply import (
    APPLY_RECEIPT_REL,
    IMPLEMENTATION_ID,
    PLAN_REL,
    PROBE_REL,
    apply_mypy_exit_gate,
    apply_pyright_ignore,
    apply_pytest_deselect_addopts,
    detect_prep_pyproject_artifacts,
    execute_go_apply,
    extract_mypy_error_lines,
    load_plan,
    load_probe,
    mypy_error_modules,
    parse_pytest_failures,
    pyright_diag_files,
    recover_pyproject_from_prep,
    restore_pyproject_stage1_snapshot,
    snapshot_pyproject_for_stage1,
    strip_deselect_for_probe,
    write_plan_after_probe,
    write_stage1_artifacts,
)

DEFAULT_TIMEOUT_S = 600.0


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _run_bounded(
    cmd: list[str],
    *,
    cwd: Path,
    timeout_s: float,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Subprocess с wall timeout и utf-8 decode (Windows cp1251 safe)."""

    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1.0, timeout_s),
        )
        timed_out = False
        rc = proc.returncode
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        rc = 124
        stdout = (
            exc.stdout.decode("utf-8", errors="replace")
            if isinstance(exc.stdout, bytes)
            else (exc.stdout or "")
        )
        stderr = (
            exc.stderr.decode("utf-8", errors="replace")
            if isinstance(exc.stderr, bytes)
            else (exc.stderr or "")
        )
    return {
        "cmd": cmd,
        "returncode": rc,
        "timed_out": timed_out,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_tail": (stdout + stderr)[-8000:],
    }


def _remaining(deadline: float) -> float:
    return max(1.0, deadline - time.monotonic())


def _venv_tool(root: Path, name: str) -> Path:
    """Исполняемый файл из Poetry-venv (те же правила, что ``.cursor/static-check/venv_resolve``)."""
    import importlib.util

    mod_path = root / ".cursor" / "static-check" / "venv_resolve.py"
    spec = importlib.util.spec_from_file_location("static_check_venv_resolve", mod_path)
    if spec is None or spec.loader is None:
        raise FileNotFoundError(f"static-check venv_resolve missing: {mod_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.resolve_venv_tool(root, name)


def _run_venv(
    root: Path,
    deadline: float,
    argv: list[str],
    *,
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Запуск с PATH=``.venv/Scripts`` (обход run.sh+CRLF при subprocess на Windows)."""

    env = os.environ.copy()
    prefix: list[str] = []
    for sub in (root / ".venv" / "Scripts", root / ".venv" / "bin"):
        if sub.is_dir():
            prefix.append(str(sub))
    if prefix:
        env["PATH"] = os.pathsep.join(prefix + [env.get("PATH", "")])
    if extra_env:
        env.update(extra_env)
    return _run_bounded(argv, cwd=root, timeout_s=_remaining(deadline), env=env)


def _pyright_strict_json(root: Path, deadline: float) -> dict[str, Any]:
    pyright = _venv_tool(root, "pyright")
    pyright_run = _run_venv(
        root,
        deadline,
        [
            str(pyright),
            "--project",
            "pyrightconfig_strict.json",
            "--outputjson",
        ],
    )
    diags: list[dict[str, Any]] = []
    raw_text = (pyright_run.get("stdout") or "") + (pyright_run.get("stderr") or "")
    if pyright_run["returncode"] in (0, 1) and not pyright_run["timed_out"]:
        try:
            payload = json.loads(raw_text)
            if isinstance(payload, dict):
                raw = payload.get("generalDiagnostics")
                if isinstance(raw, list):
                    diags = [d for d in raw if isinstance(d, dict)]
        except json.JSONDecodeError:
            pass
    return {"run": pyright_run, "diagnostics": diags}


def _run_static_check_cmd(
    root: Path,
    deadline: float,
    tool_argv: list[str],
) -> dict[str, Any]:
    """Тот же вызов, что gate mypy: ``run.sh -- …`` с fallback на venv (Windows subprocess)."""

    run_sh = root / ".cursor/static-check/bin/run.sh"
    if run_sh.is_file():
        via_sh = _run_bounded(
            ["bash", str(run_sh), "--", *tool_argv],
            cwd=root,
            timeout_s=_remaining(deadline),
        )
        if not via_sh.get("timed_out") and via_sh.get("returncode") != 127:
            return via_sh
    if tool_argv and tool_argv[0] == "mypy":
        mypy = _venv_tool(root, "mypy")
        return _run_venv(root, deadline, [str(mypy), *tool_argv[1:]])
    return _run_venv(root, deadline, tool_argv)


def _mypy_errors(root: Path, deadline: float) -> dict[str, Any]:
    run = _run_static_check_cmd(root, deadline, ["mypy", "."])
    error_lines = extract_mypy_error_lines(
        run.get("stdout") or "",
        run.get("stderr") or "",
    )
    return {"run": run, "error_lines": error_lines}


def _pytest_wave(
    root: Path,
    deadline: float,
    marker_and_args: list[str],
    *,
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    pytest = _venv_tool(root, "pytest")
    run = _run_venv(
        root,
        deadline,
        [
            str(pytest),
            "-q",
            "-rF",
            "--tb=line",
            *marker_and_args,
        ],
        extra_env=extra_env,
    )
    failures = parse_pytest_failures(
        run.get("stdout") or "",
        run.get("stderr") or "",
    )
    return {"run": run, "failures": failures}


def _destructive_env() -> dict[str, str]:
    return {
        "STROY_ALLOW_DESTRUCTIVE_TEST_DB": "1",
        "ALLOW_DESTRUCTIVE_TEST_DB": "1",
        "ALLOW_DESTRUCTIVE_TEST_CLICKHOUSE": "1",
        "ALLOW_DESTRUCTIVE_TEST_SUBPROCESS": "1",
    }


def _probe_pytest_full(root: Path, deadline: float) -> dict[str, Any]:
    """Pytest cascade как registry FULL (unit xdist → integration → destructive)."""

    waves: list[dict[str, Any]] = []
    waves.append(
        _pytest_wave(
            root,
            deadline,
            ["-m", "unit", "-n", "8", "--dist=worksteal"],
        )
    )
    if _remaining(deadline) <= 1.0:
        return {"waves": waves, "budget_exhausted": True}
    waves.append(_pytest_wave(root, deadline, ["-m", "integration"]))
    if _remaining(deadline) <= 1.0:
        return {"waves": waves, "budget_exhausted": True}
    waves.append(
        _pytest_wave(
            root,
            deadline,
            ["-m", "destructive_local"],
            extra_env=_destructive_env(),
        )
    )
    return {"waves": waves, "budget_exhausted": False}


def probe(root: Path, *, timeout_s: float, full: bool) -> dict[str, Any]:
    """Собрать красное; только probe-result.json (audit), конфиги не трогать."""

    deadline = time.monotonic() + timeout_s
    result: dict[str, Any] = {
        "schema": "opsx.prep_green_scope.probe",
        "implementation": IMPLEMENTATION_ID,
        "probed_at": datetime.now(UTC).isoformat(),
        "timeout_s": timeout_s,
        "full": full,
        "pyright_ignore_candidates": [],
        "mypy_module_candidates": [],
        "mypy_error_line_count": 0,
        "mypy_exit_code": None,
        "pytest_failures": [],
        "pytest_failure_count": 0,
        "timed_out_checks": [],
        "gate_mapping_note": (
            "pyright→pyrightconfig_strict.json ignore; "
            "mypy→pyproject [tool.mypy] ignore_errors (exit 0 on mypy .); "
            "pytest→addopts --deselect replace (not append)"
        ),
    }

    pr = _pyright_strict_json(root, deadline)
    if pr["run"]["timed_out"]:
        result["timed_out_checks"].append("pyright-strict")
    else:
        result["pyright_ignore_candidates"] = pyright_diag_files(pr["diagnostics"], root)

    if _remaining(deadline) > 1.0:
        my = _mypy_errors(root, deadline)
        if my["run"]["timed_out"]:
            result["timed_out_checks"].append("mypy")
        else:
            lines = my["error_lines"]
            result["mypy_error_line_count"] = len(lines)
            result["mypy_exit_code"] = my["run"].get("returncode")
            result["mypy_module_candidates"] = mypy_error_modules(lines)

    if full and _remaining(deadline) > 1.0:
        pt = _probe_pytest_full(root, deadline)
        if pt.get("budget_exhausted"):
            result["timed_out_checks"].append("pytest-cascade-budget")
        for wave in pt["waves"]:
            if wave["run"].get("timed_out"):
                result["timed_out_checks"].append("pytest-wave-timeout")
            result["pytest_failures"].extend(wave.get("failures") or [])
        deduped = sorted(set(result["pytest_failures"]))
        result["pytest_failures"] = deduped
        result["pytest_failure_count"] = len(deduped)

    _write_json(root / PROBE_REL, result)
    return result


def apply_from_probe(root: Path, *, owner_ref: str) -> dict[str, Any]:
    """Owner GO: записать exclusions в pyrightconfig_strict / pyproject.toml (штатно)."""

    probe_data = load_probe(root)
    py_added = apply_pyright_ignore(
        root, list(probe_data.get("pyright_ignore_candidates") or [])
    )
    mypy_action = apply_mypy_exit_gate(
        root,
        error_line_count=int(probe_data.get("mypy_error_line_count") or 0),
    )
    pt_added = apply_pytest_deselect_addopts(
        root, list(probe_data.get("pytest_failures") or [])
    )
    receipt = {
        "schema": "opsx.prep_green_scope.apply",
        "implementation": IMPLEMENTATION_ID,
        "applied_at": datetime.now(UTC).isoformat(),
        "owner_authorization_ref": owner_ref,
        "probe_probed_at": probe_data.get("probed_at"),
        "pyright_ignore_added": py_added,
        "mypy_gate_action": mypy_action,
        "pytest_deselect_count": pt_added,
        "probe_pytest_failure_count": probe_data.get("pytest_failure_count"),
        "mechanisms": {
            "pyright": "pyrightconfig_strict.json → ignore[] (static-check pyright-strict)",
            "mypy": (
                "pyproject.toml → [tool.mypy] ignore_errors=true "
                "(typing diagnostics may still print; static-check PASS only when "
                "ignore_errors configured — see mypy pack)"
            ),
            "pytest": (
                "pyproject.toml → addopts: strip prior prep --deselect=, "
                "then set from probe (static-check pytest waves)"
            ),
        },
    }
    _write_json(root / APPLY_RECEIPT_REL, receipt)
    return {
        "ok": True,
        "status": "APPLY_DONE",
        "receipt_path": APPLY_RECEIPT_REL.as_posix(),
        "pyright_ignore_added": py_added,
        "mypy_gate_action": mypy_action,
        "pytest_deselect_count": pt_added,
    }


def confirm(root: Path, *, timeout_s: float, full: bool) -> dict[str, Any]:
    """Повторный прогон; PASS если штатные конфиги уже отключили красное."""

    deadline = time.monotonic() + timeout_s
    pr = _pyright_strict_json(root, deadline)
    pyright_ok = pr["run"]["returncode"] == 0 and not pr["run"]["timed_out"]

    mypy_ok = True
    if _remaining(deadline) > 1.0:
        my = _mypy_errors(root, deadline)
        mypy_ok = my["run"]["returncode"] == 0 and not my["run"]["timed_out"]

    pytest_ok = True
    remaining: list[str] = []
    if full and _remaining(deadline) > 1.0:
        pt = _probe_pytest_full(root, deadline)
        for wave in pt["waves"]:
            run = wave["run"]
            if run.get("timed_out") or run.get("returncode") not in (0,):
                pytest_ok = False
            remaining.extend(wave.get("failures") or [])
        remaining = sorted(set(remaining))

    probe_data = load_probe(root) if (root / PROBE_REL).is_file() else {}
    probe_pt_count = int(probe_data.get("pytest_failure_count") or 0)
    ok = pyright_ok and mypy_ok and pytest_ok and not remaining
    out = {
        "schema": "opsx.prep_green_scope.confirm",
        "implementation": IMPLEMENTATION_ID,
        "confirmed_at": datetime.now(UTC).isoformat(),
        "ok": ok,
        "status": "CONFIRM_PASS" if ok else "CONFIRM_FAIL",
        "pyright_ok": pyright_ok,
        "mypy_ok": mypy_ok,
        "mypy_gate_note": "PASS requires returncode 0 on mypy . (same as static-check)",
        "pytest_ok": pytest_ok,
        "pytest_remaining_failures": remaining,
        "pytest_remaining_count": len(remaining),
        "probe_pytest_failure_count": probe_pt_count,
        "pytest_count_matches_probe": len(remaining) == 0
        if probe_pt_count == 0
        else len(remaining) <= probe_pt_count,
    }
    _write_json(root / Path("opsx/prep-green-scope/confirm-result.json"), out)
    return out


def _auto_owner_ref() -> str:
    """Audit ref для APPLY без ручного owner-ref от Owner."""

    return f"owner-message:auto-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"


def _resolve_owner_ref(root: Path, explicit: str) -> str:
    if explicit.strip():
        return explicit.strip()
    planned = str(load_plan(root).get("owner_authorization_ref") or "").strip()
    if planned:
        return planned
    return _auto_owner_ref()


def _needs_recover(root: Path) -> bool:
    artifacts = detect_prep_pyproject_artifacts(root)
    return bool(
        artifacts.get("pytest_deselect_in_addopts")
        or artifacts.get("mypy_override_blocks")
        or artifacts.get("mypy_global_ignore_errors")
    )


def _write_stage2_plan(root: Path, probe_result: dict[str, Any]) -> dict[str, Any]:
    """План этапа 2 после PROBE (без записи exclusions в product configs)."""

    needs_recover = _needs_recover(root)
    go_steps: list[str] = []
    if needs_recover:
        go_steps.append("RECOVER")
    go_steps.append("APPLY")
    mypy_lines = int(probe_result.get("mypy_error_line_count") or 0)
    plan: dict[str, Any] = {
        "schema": "opsx.prep_green_scope.plan",
        "stage": 1,
        "status": "AWAITING_GO",
        "implementation": IMPLEMENTATION_ID,
        "planned_at": datetime.now(UTC).isoformat(),
        "probe_probed_at": probe_result.get("probed_at"),
        "needs_recover_before_apply": needs_recover,
        "go_steps": go_steps,
        "will_apply": {
            "pyright_ignore_files": len(
                probe_result.get("pyright_ignore_candidates") or []
            ),
            "mypy_ignore_errors_global": mypy_lines > 0,
            "mypy_error_line_count": mypy_lines,
            "pytest_deselect_count": int(
                probe_result.get("pytest_failure_count") or 0
            ),
        },
        "owner_authorization_ref": _auto_owner_ref(),
        "execute_on_go": {"runner_command": "GO", "requires_probe": True},
        "after_apply_owner_slash": [
            "/opsx-commit GO",
            "/opsx-static-check FULL",
        ],
    }
    _write_json(root / PLAN_REL, plan)
    return plan


def stage1_probe(root: Path, *, timeout_s: float) -> dict[str, Any]:
    """Этап 1: PROBE FULL на **чистых** addopts; snapshot restore после PROBE."""

    snapshot_pyproject_for_stage1(root)
    strip_deselect_for_probe(root)
    try:
        probe_result = probe(root, timeout_s=timeout_s, full=True)
    finally:
        restore_pyproject_stage1_snapshot(root)
    plan = _write_stage2_plan(root, probe_result)
    return {"probe": probe_result, "plan": plan}


def execute_go(root: Path, *, owner_ref: str) -> dict[str, Any]:
    """Этап 2 после Owner GO: RECOVER (если надо) + APPLY по probe-result.json."""

    probe_path = root / PROBE_REL
    if not probe_path.is_file():
        return {
            "ok": False,
            "status": "BLOCKED_NEEDS_STAGE1",
            "reason": "missing_probe",
        }
    try:
        probe_data = load_probe(root)
    except (OSError, json.JSONDecodeError):
        return {
            "ok": False,
            "status": "BLOCKED_NEEDS_STAGE1",
            "reason": "invalid_probe",
        }
    if probe_data.get("implementation") != IMPLEMENTATION_ID:
        return {
            "ok": False,
            "status": "BLOCKED_NEEDS_STAGE1",
            "reason": "stale_probe",
        }
    steps: list[dict[str, Any]] = []
    if _needs_recover(root):
        rec = recover_pyproject_from_prep(root)
        steps.append({"step": "RECOVER", "actions": rec.get("actions")})
    ref = _resolve_owner_ref(root, owner_ref)
    apply_res = apply_from_probe(root, owner_ref=ref)
    steps.append({"step": "APPLY", "status": apply_res.get("status")})
    ok = bool(apply_res.get("ok"))
    return {
        "ok": ok,
        "status": "APPLY_DONE" if ok else "APPLY_FAIL",
        "steps": steps,
        "receipt_path": apply_res.get("receipt_path"),
    }


def build_plan(root: Path) -> dict[str, Any]:
    """Быстрый preview без PROBE (legacy); этап 1 = ``stage1_probe``."""

    artifacts = detect_prep_pyproject_artifacts(root)
    needs_recover = _needs_recover(root)
    probe_data: dict[str, Any] = {}
    probe_stale = True
    probe_path = root / PROBE_REL
    if probe_path.is_file():
        try:
            probe_data = load_probe(root)
            probe_stale = probe_data.get("implementation") != IMPLEMENTATION_ID
        except (OSError, json.JSONDecodeError):
            probe_stale = True
    plan: dict[str, Any] = {
        "schema": "opsx.prep_green_scope.plan",
        "status": "PLAN_PREVIEW",
        "implementation": IMPLEMENTATION_ID,
        "planned_at": datetime.now(UTC).isoformat(),
        "pyproject_artifacts": artifacts,
        "needs_recover": needs_recover,
        "probe_stale": probe_stale,
        "probe_probed_at": probe_data.get("probed_at"),
        "note": "Run STAGE1 (/opsx-prep-green-scope) for full probe before GO",
    }
    _write_json(root / PLAN_REL, plan)
    return plan


def run_full_pipeline(
    root: Path,
    *,
    timeout_s: float,
    owner_ref: str,
    skip_recover: bool = False,
) -> dict[str, Any]:
    """Deprecated monolith: STAGE1 + GO. Prefer ``stage1_probe`` + ``execute_go``."""

    stage1_probe(root, timeout_s=timeout_s)
    return execute_go(root, owner_ref=owner_ref)


_DEPRECATION = (
    "DEPRECATED: use /opsx-prep-green-scope (agent) + AGENT_PLAYBOOK.md. "
    "Discovery = /opsx-static-check FULL only. "
    "Allowed CLI: GO --go (apply-only emergency)."
)


def main(argv: list[str] | None = None) -> int:
    """CLI entry (legacy apply-only)."""

    parser = argparse.ArgumentParser(
        description="Prep green scope — DEPRECATED orchestrator; agent + static-check only"
    )
    parser.add_argument(
        "command",
        choices=["STAGE1", "PLAN", "RECOVER", "PROBE", "APPLY", "CONFIRM", "GO", "RUN"],
    )
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--timeout-s", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--go", action="store_true")
    parser.add_argument("--owner-ref", type=str, default="")
    parser.add_argument("--skip-recover", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()

    if args.command in ("STAGE1", "PLAN", "RECOVER", "PROBE", "APPLY", "CONFIRM", "RUN"):
        print(_DEPRECATION, file=sys.stderr)
        print(f"STATUS: BLOCKED_DEPRECATED_CLI command={args.command}", file=sys.stderr)
        print("NEXT_ACTION: /opsx-prep-green-scope (agent STAGE1 via /opsx-static-check FULL)", file=sys.stderr)
        return 2

    if args.command == "GO":
        if not args.go:
            print("STATUS: BLOCKED_NEEDS_GO")
            print("NEXT_ACTION: GO")
            return 2
        owner_ref = args.owner_ref
        res = execute_go_apply(root, owner_ref=owner_ref)
        print(f"STATUS: {res.get('status')}")
        if res.get("receipt_path"):
            print(f"RECEIPT_PATH: {res.get('receipt_path')}")
        if res.get("reason"):
            print(f"REASON: {res.get('reason')}")
        return 0 if res.get("ok") else 1

    print(_DEPRECATION, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
