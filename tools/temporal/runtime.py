#!/usr/bin/env python
"""Bootstrap and enter the isolated Temporal/Cursor SDK runtime."""

from __future__ import annotations

from opsx.project_context import resource_path

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]

# Imported lazily in main() to avoid circular imports during bootstrap.
def _apply_bridge_temp_env() -> None:
    from config import apply_cursor_sdk_bridge_temp_dir

    apply_cursor_sdk_bridge_temp_dir()
VENV = HERE / ".venv"
OPERATOR = HERE / "tempo_operator.py"
TEMPORAL_REQUIREMENTS = HERE / "requirements.txt"
EXECUTOR_ROOT = resource_path(REPO_ROOT, "tools", "cursor_executor")
EXECUTOR_REQUIREMENTS = EXECUTOR_ROOT / "requirements.txt"
EXECUTOR_PYPROJECT = EXECUTOR_ROOT / "pyproject.toml"
MARKER = VENV / "opsx-runtime.json"
EXPECTED_PYTHON = (3, 12)


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _committed_or_worktree_hash(path: Path) -> str:
    """Use canonical Git blob bytes for clean tracked inputs; dirty inputs bind to worktree bytes."""

    try:
        relative = path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return _hash_file(path)

    unstaged = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "diff", "--quiet", "HEAD", "--", relative],
        check=False,
    )
    staged = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "diff", "--cached", "--quiet", "HEAD", "--", relative],
        check=False,
    )
    if unstaged.returncode == 0 and staged.returncode == 0:
        blob = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "show", f"HEAD:{relative}"],
            capture_output=True,
            check=False,
        )
        if blob.returncode == 0:
            return hashlib.sha256(blob.stdout).hexdigest()
    return _hash_file(path)


def runtime_fingerprint() -> tuple[str, dict[str, str]]:
    inputs = {
        "python": ".".join(map(str, EXPECTED_PYTHON)),
        "temporal_requirements": _committed_or_worktree_hash(TEMPORAL_REQUIREMENTS),
        "executor_requirements": _committed_or_worktree_hash(EXECUTOR_REQUIREMENTS),
        "executor_pyproject": _committed_or_worktree_hash(EXECUTOR_PYPROJECT),
    }
    encoded = json.dumps(inputs, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), inputs


def runtime_python() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def _python312_prefix() -> list[str]:
    candidates: list[list[str]] = []
    if sys.version_info[:2] == EXPECTED_PYTHON:
        candidates.append([sys.executable])
    py_launcher = shutil.which("py")
    if py_launcher:
        candidates.append([py_launcher, "-3.12"])
    python312 = shutil.which("python3.12")
    if python312:
        candidates.append([python312])
    for prefix in candidates:
        probe = subprocess.run(
            [*prefix, "-c", "import sys; raise SystemExit(sys.version_info[:2] != (3, 12))"],
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode == 0:
            return prefix
    raise RuntimeError("PYTHON_3_12_UNAVAILABLE")


def _probe_runtime(python: Path) -> bool:
    expected_executor = (EXECUTOR_ROOT / "cursor_executor").resolve()
    code = (
        "import importlib.metadata as m, pathlib, sys, cursor_executor; "
        f"expected=pathlib.Path({str(expected_executor)!r}).resolve(); "
        "origin=pathlib.Path(cursor_executor.__file__).resolve(); "
        "origin_ok=(origin.parent==expected); "
        "ok=sys.version_info[:2]==(3,12) "
        "and m.version('cursor-sdk')=='1.0.31' "
        "and m.version('temporalio')=='1.33.0' "
        "and origin_ok; "
        "raise SystemExit(0 if ok else 3)"
    )
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(EXECUTOR_ROOT)
        if not existing_pythonpath
        else os.pathsep.join((str(EXECUTOR_ROOT), existing_pythonpath))
    )
    result = subprocess.run(
        [str(python), "-c", code],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    return result.returncode == 0


def _marker_matches() -> bool:
    if not MARKER.is_file():
        return False
    try:
        marker = json.loads(MARKER.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    fingerprint, _ = runtime_fingerprint()
    return marker.get("fingerprint") == fingerprint


def runtime_ready() -> bool:
    python = runtime_python()
    return python.is_file() and _marker_matches() and _probe_runtime(python)


def bootstrap_runtime() -> Path:
    python = runtime_python()
    if not python.is_file():
        if VENV.exists():
            shutil.rmtree(VENV)
        prefix = _python312_prefix()
        subprocess.run([*prefix, "-m", "venv", str(VENV)], check=True)
        python = runtime_python()

    requirements = [
        line.strip()
        for line in TEMPORAL_REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    for requirement in requirements:
        subprocess.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                requirement,
            ],
            check=True,
            timeout=180,
        )
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "cursor-sdk==1.0.31",
        ],
        check=True,
        timeout=180,
    )
    fingerprint, inputs = runtime_fingerprint()
    payload = {
        "schema_version": "opsx-temporal-sdk-runtime-v1",
        "fingerprint": fingerprint,
        "python": "3.12",
        "cursor_sdk": "1.0.31",
        "cursor_executor": "0.1.0",
        "temporalio": "1.33.0",
        "inputs": inputs,
    }
    tmp = MARKER.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, MARKER)
    if not _probe_runtime(python):
        raise RuntimeError("SDK_RUNTIME_PROBE_FAILED_AFTER_BOOTSTRAP")
    return python


def ensure_runtime() -> Path:
    if runtime_ready():
        return runtime_python()
    return bootstrap_runtime()


def _normalize_operator_argv(args: list[str]) -> list[str]:
    """``tempo_operator`` requires global ``--repo`` before the subcommand."""

    repo: str | None = None
    rest: list[str] = []
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--repo" and index + 1 < len(args):
            repo = args[index + 1]
            index += 2
            continue
        rest.append(token)
        index += 1
    if repo:
        return ["--repo", repo, *rest]
    return rest


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    _apply_bridge_temp_env()
    try:
        python = ensure_runtime()
    except Exception as exc:
        print(json.dumps({"status": "BLOCKED_RUNTIME", "reason": str(exc)}))
        return 2
    if args == ["--ensure-only"]:
        fingerprint, _ = runtime_fingerprint()
        print(
            json.dumps(
                {
                    "status": "READY",
                    "runtime": "temporal-cursor-sdk",
                    "python": str(python),
                    "fingerprint": fingerprint,
                }
            )
        )
        return 0

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    for key in ("TEMP", "TMP", "TMPDIR"):
        if key in os.environ:
            env[key] = os.environ[key]
    return subprocess.call(
        [str(python), str(OPERATOR), *_normalize_operator_argv(args)],
        cwd=str(HERE),
        env=env,
    )


if __name__ == "__main__":
    raise SystemExit(main())
