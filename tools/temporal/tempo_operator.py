#!/usr/bin/env python3
"""Shared process-operator helpers for /tempo-* Cursor commands.

One pipeline per command. This module does NOT run hunt/orch itself: it
prepares models, keeps a single Temporal worker, and reports workflow health
so the command can monitor and repair the *process*.
"""

from __future__ import annotations

from opsx.project_context import resource_path

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from config import (
    TEMPORAL_ADDRESS,
    TEMPORAL_TASK_QUEUE,
    TEMPORAL_UI_URL,
    apply_cursor_sdk_bridge_temp_dir,
)
from control_plane_code_identity import compute_control_plane_code_identity
from model_config import (
    ALLOWED_TIERS,
    DEFAULT_MODEL_CONFIG_PATH,
    DEFAULT_OVERLAY_REL,
    parse_tier_pair,
    write_step_overlay,
)
from owner_chrome_focus_gate import (
    STEP_ID as FOCUS_GATE_STEP_ID,
    assignment_from_ledger,
    evaluate_pre_run_gate,
    write_focus_ack,
)
from owner_browser_check_gate import (
    GO_STEP_ID,
    format_chat_instruction,
    release_page_file_url,
    run_browser_check_release,
    write_go_ack,
)
from owner_done_signal import STEP_ID, done_page_file_url, run_post_completion_steps
from owner_monitoring import (
    artifacts_monitoring_path,
    build_monitoring_manifest,
    write_owner_monitoring_manifest,
)
from singleton import PipelineLock, pipeline_lock_path

PIPELINES_PATH = HERE / "pipelines.json"
EVENT_ACTIVITY_SCHEDULED = 10
EVENT_ACTIVITY_COMPLETED = 12
EVENT_ACTIVITY_FAILED = 13


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def load_pipelines() -> dict[str, Any]:
    return json.loads(PIPELINES_PATH.read_text(encoding="utf-8"))


def get_pipeline(pipeline_id: str) -> dict[str, Any]:
    data = load_pipelines()
    pipelines = data.get("pipelines") or {}
    spec = pipelines.get(pipeline_id)
    if not spec:
        known = ", ".join(sorted(pipelines))
        raise KeyError(f"Unknown pipeline {pipeline_id!r}; expected one of: {known}")
    return spec


def git_current_branch(repo: Path) -> str:
    """Return the current git branch name for repo (HEAD)."""
    result = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=str(repo),
        check=False,
        capture_output=True,
        text=True,
    )
    branch = (result.stdout or "").strip()
    if result.returncode != 0 or not branch:
        raise RuntimeError(
            "Cannot resolve current branch (detached HEAD or not a git repo). "
            f"git stderr: {(result.stderr or '').strip()}"
        )
    return branch


def parse_key_value_token(token: str) -> tuple[str, str] | None:
    if "=" not in token:
        return None
    key, value = token.split("=", 1)
    key = key.strip().lower()
    value = value.strip()
    if not key or not value:
        return None
    return key, value


def resolve_project_bug_hunt_args(
    repo: Path,
    *,
    change: str | None,
    base: str | None = None,
) -> dict[str, str]:
    """Resolve hunt tokens: change required, base default stage, head = current branch."""
    spec = get_pipeline("project-bug-hunt")
    resolved_base = (base or spec.get("defaults", {}).get("base") or "stage").strip()
    if not change or not change.strip():
        raise ValueError("change= is required (OpenSpec change id)")
    resolved_change = change.strip()
    resolved_head = git_current_branch(repo)
    return {
        "base": resolved_base,
        "head": resolved_head,
        "change": resolved_change,
    }


def parse_project_bug_hunt_cli_tokens(tokens: list[str]) -> dict[str, Any]:
    """Parse tempo-project-bug-hunt: change=/base= first, review/repair tiers last."""
    if len(tokens) < 3:
        raise ValueError(
            "usage: /tempo-project-bug-hunt change=<id> [base=stage] <review-tier> <repair-tier>"
        )
    review, repair = parse_tier_pair(tokens[-2], tokens[-1])
    change: str | None = None
    base: str | None = None
    for raw in tokens[:-2]:
        if raw.strip().lower().startswith("head="):
            raise ValueError("head is always the current git branch; do not pass head=")
        parsed = parse_key_value_token(raw)
        if not parsed:
            raise ValueError(
                f"Unknown token {raw!r}; use change=<id>, optional base=<branch>, then two model tiers"
            )
        key, value = parsed
        if key == "change":
            change = value
        elif key == "base":
            base = value
        else:
            raise ValueError(f"Unknown key {key!r}; allowed: change=, base=")
    return {"review": review, "repair": repair, "change": change, "base": base}


def resolve_bugbot_args(
    repo: Path,
    *,
    base: str | None = None,
) -> dict[str, str]:
    """Resolve bugbot tokens: base default stage, head = current branch."""
    spec = get_pipeline("bugbot")
    resolved_base = (base or spec.get("defaults", {}).get("base") or "stage").strip()
    resolved_head = git_current_branch(repo)
    return {
        "base": resolved_base,
        "head": resolved_head,
    }


def parse_bugbot_cli_tokens(tokens: list[str]) -> dict[str, Any]:
    """Parse tempo-bugbot: optional base= first, review/repair tiers last."""
    if len(tokens) < 2:
        raise ValueError(
            "usage: /tempo-bugbot [base=stage] <review-tier> <repair-tier>"
        )
    review, repair = parse_tier_pair(tokens[-2], tokens[-1])
    base: str | None = None
    for raw in tokens[:-2]:
        if raw.strip().lower().startswith("head="):
            raise ValueError("head is always the current git branch; do not pass head=")
        if raw.strip().lower().startswith("change="):
            raise ValueError("bugbot pipeline does not accept change=; use project-bug-hunt")
        parsed = parse_key_value_token(raw)
        if not parsed:
            raise ValueError(f"Unknown token {raw!r}; use optional base=<branch>, then two model tiers")
        key, value = parsed
        if key == "base":
            base = value
        else:
            raise ValueError(f"Unknown key {key!r}; allowed: base=")
    return {"review": review, "repair": repair, "base": base}


def resolve_security_args(
    repo: Path,
    *,
    base: str | None = None,
) -> dict[str, str]:
    """Resolve security tokens: base default stage, head = current branch."""
    spec = get_pipeline("security")
    resolved_base = (base or spec.get("defaults", {}).get("base") or "stage").strip()
    resolved_head = git_current_branch(repo)
    return {
        "base": resolved_base,
        "head": resolved_head,
    }


def parse_security_cli_tokens(tokens: list[str]) -> dict[str, Any]:
    """Parse tempo-security: optional base= first, review/repair tiers last."""
    if len(tokens) < 2:
        raise ValueError(
            "usage: /tempo-security [base=stage] <review-tier> <repair-tier>"
        )
    review, repair = parse_tier_pair(tokens[-2], tokens[-1])
    base: str | None = None
    for raw in tokens[:-2]:
        if raw.strip().lower().startswith("head="):
            raise ValueError("head is always the current git branch; do not pass head=")
        if raw.strip().lower().startswith("change="):
            raise ValueError("security pipeline does not accept change=")
        parsed = parse_key_value_token(raw)
        if not parsed:
            raise ValueError(f"Unknown token {raw!r}; use optional base=<branch>, then two model tiers")
        key, value = parsed
        if key == "base":
            base = value
        else:
            raise ValueError(f"Unknown key {key!r}; allowed: base=")
    return {"review": review, "repair": repair, "base": base}


def resolve_verification_args(
    repo: Path,
    *,
    change: str | None,
) -> dict[str, str]:
    """Resolve verification tokens: change required; no base/head."""
    del repo  # unused — ledger/plan are change-scoped
    if not change or not change.strip():
        raise ValueError("change= is required (OpenSpec change id)")
    return {"change": change.strip()}


def parse_verification_cli_tokens(tokens: list[str]) -> dict[str, Any]:
    """Parse tempo-verification: change= required; next_check/executor tiers last."""
    if len(tokens) < 3:
        raise ValueError(
            "usage: /tempo-verification change=<id> <next-check-tier> <executor-tier>"
        )
    review, repair = parse_tier_pair(tokens[-2], tokens[-1])
    change: str | None = None
    for raw in tokens[:-2]:
        if raw.strip().lower().startswith("head="):
            raise ValueError("verification does not accept head=")
        if raw.strip().lower().startswith("base="):
            raise ValueError("verification does not accept base=; durable plan/ledger own scope")
        parsed = parse_key_value_token(raw)
        if not parsed:
            raise ValueError(
                f"Unknown token {raw!r}; use change=<id>, then two model tiers"
            )
        key, value = parsed
        if key == "change":
            change = value
        else:
            raise ValueError(f"Unknown key {key!r}; allowed: change=")
    return {"review": review, "repair": repair, "change": change, "base": None}


def _parse_run_request(
    pipeline_id: str,
    repo: Path,
    *,
    review: str,
    repair: str,
    change: str | None,
    base: str | None,
) -> tuple[dict[str, Any], dict[str, str], dict[str, Any]]:
    spec = get_pipeline(pipeline_id)
    if pipeline_id == "project-bug-hunt":
        kv_tokens: list[str] = []
        if change:
            kv_tokens.append(f"change={change}")
        if base:
            kv_tokens.append(f"base={base}")
        parsed = parse_project_bug_hunt_cli_tokens([*kv_tokens, review, repair])
        resolved = resolve_project_bug_hunt_args(
            repo,
            change=parsed["change"],
            base=parsed["base"],
        )
    elif pipeline_id == "bugbot":
        kv_tokens = [f"base={base}"] if base else []
        parsed = parse_bugbot_cli_tokens([*kv_tokens, review, repair])
        resolved = resolve_bugbot_args(repo, base=parsed["base"])
        if change:
            resolved["adversarial_change_id"] = change.strip()
    elif pipeline_id == "security":
        kv_tokens = [f"base={base}"] if base else []
        parsed = parse_security_cli_tokens([*kv_tokens, review, repair])
        resolved = resolve_security_args(repo, base=parsed["base"])
        if change:
            resolved["adversarial_change_id"] = change.strip()
    elif pipeline_id == "verification":
        kv_tokens = [f"change={change}"] if change else []
        parsed = parse_verification_cli_tokens([*kv_tokens, review, repair])
        resolved = resolve_verification_args(repo, change=parsed["change"])
    else:
        raise ValueError(f"Unsupported pipeline {pipeline_id!r}")
    return parsed, resolved, spec


def build_start_cmd(
    spec: dict[str, Any],
    *,
    repo: Path,
    overlay: Path,
    review: str,
    repair: str,
    resolved: dict[str, str],
) -> list[str]:
    repo_arg = str(repo).replace("\\", "/")
    cmd = [
        sys.executable,
        "-u",
        "start.py",
        "--mode",
        "temporal-real",
        "--lane",
        spec["lane"],
        "--repo",
        repo_arg,
        "--model-config",
        str(overlay),
        "--review-tier",
        review,
        "--repair-tier",
        repair,
    ]
    if "base" in resolved:
        cmd.extend(["--base", resolved["base"]])
    if "head" in resolved:
        cmd.extend(["--head", resolved["head"]])
    if "change" in resolved:
        cmd.extend(["--change", resolved["change"]])
    return cmd


def overlay_path(repo: Path) -> Path:
    return resource_path(repo, DEFAULT_OVERLAY_REL)


def _cmd_resolve_args(args: argparse.Namespace) -> int:
    repo = Path(args.repo)
    pipeline_id = args.pipeline
    if pipeline_id not in ("bugbot", "security", "project-bug-hunt", "verification"):
        raise SystemExit(
            f"resolve-args supports bugbot|security|project-bug-hunt|verification; got {pipeline_id!r}"
        )
    try:
        parsed, resolved, _spec = _parse_run_request(
            pipeline_id,
            repo,
            review=args.review,
            repair=args.repair,
            change=args.change,
            base=args.base,
        )
    except ValueError as exc:
        usage = {
            "bugbot": "/tempo-bugbot [base=stage] <review> <repair>",
            "security": "/tempo-security [base=stage] <review> <repair>",
            "project-bug-hunt": (
                "/tempo-project-bug-hunt change=<id> [base=stage] <review> <repair>"
            ),
            "verification": (
                "/tempo-verification change=<id> <next-check-tier> <executor-tier>"
            ),
        }[pipeline_id]
        _print_json({"error": str(exc), "usage": usage})
        return 2
    _print_json(
        {
            "review": parsed["review"],
            "repair": parsed["repair"],
            **resolved,
        }
    )
    return 0


def _cursor_cloud_agent_runtime() -> bool:
    """Хост похож на Cursor Cloud Agent (env или типичный layout VM)."""
    if os.environ.get("CURSOR_CLOUD") == "1" or os.environ.get("CURSOR_AGENT"):
        return True
    return Path("/opt/cursor/artifacts").is_dir()


def _temporal_cloud_compose_enabled() -> bool:
    """Включить cloud override compose (host-gateway к Postgres на :5435).

    Явный ``STROY_TEMPORAL_CLOUD_COMPOSE=0`` отключает override даже на Cloud VM.
    На Cursor Cloud Agent по умолчанию включаем override (bridge не достигает
    ``postgresql`` / ``temporal`` по внутреннему DNS).
    """
    flag = os.environ.get("STROY_TEMPORAL_CLOUD_COMPOSE", "").strip().lower()
    if flag in ("1", "true", "yes"):
        return True
    if flag in ("0", "false", "no"):
        return False
    return _cursor_cloud_agent_runtime()


def _docker_compose_file_args() -> list[str]:
    """Аргументы -f для docker compose (базовый файл + опциональный cloud override)."""
    args = ["-f", "docker-compose.yml"]
    if _temporal_cloud_compose_enabled():
        cloud = HERE / "docker-compose.cloud.yml"
        if cloud.is_file():
            args.extend(["-f", "docker-compose.cloud.yml"])
    return args


def _docker_permission_denied(result: subprocess.CompletedProcess[str]) -> bool:
    combined = f"{result.stderr or ''}\n{result.stdout or ''}".lower()
    return "permission denied" in combined or "got permission denied" in combined


def _docker_compose_child_env() -> dict[str, str]:
    """Переменные для подстановки в docker-compose.cloud.yml (TEMPORAL_ADDRESS UI)."""
    env = dict(os.environ)
    if _temporal_cloud_compose_enabled():
        env.setdefault("STROY_TEMPORAL_UI_GRPC_HOST", "host.docker.internal:7233")
    return env


def _run_docker_compose(*compose_args: str) -> bool:
    """Запуск docker compose; на Cloud VM без группы docker — повтор через sudo."""
    file_args = _docker_compose_file_args()
    compose_env = _docker_compose_child_env()
    # sudo -n: Cloud VM has passwordless sudo but no TTY. Без -n sudo
    # сразу завершается и compose не поднимается.
    attempts: tuple[list[str], ...] = (["docker"], ["sudo", "-n", "docker"])
    for index, prefix in enumerate(attempts):
        cmd = [*prefix, "compose", *file_args, *compose_args]
        try:
            result = subprocess.run(
                cmd,
                cwd=str(HERE),
                check=False,
                capture_output=True,
                text=True,
                env=compose_env,
            )
        except FileNotFoundError:
            if index == 0:
                continue
            return False
        if result.returncode == 0:
            return True
        if index == 0 and _docker_permission_denied(result):
            continue
        return False
    return False


def _run_docker_compose_up() -> bool:
    """Поднять Temporal stack."""
    return _run_docker_compose("up", "-d")


def _run_docker_compose_recreate_ui() -> bool:
    """Пересоздать только temporal-ui (нужно после смены TEMPORAL_ADDRESS в override)."""
    return _run_docker_compose("up", "-d", "--force-recreate", "temporal-ui")


def ensure_temporal_server(*, repair: bool = True) -> bool:
    if temporal_ui_up():
        return True
    if not repair:
        return False
    compose = HERE / "docker-compose.yml"
    if not compose.is_file():
        return False
    if _temporal_cloud_compose_enabled() and _run_docker_compose_recreate_ui():
        import time

        for _ in range(10):
            if temporal_ui_up():
                return True
            time.sleep(1)
    if not _run_docker_compose_up():
        return False
    import time

    for _ in range(15):
        if temporal_ui_up():
            return True
        time.sleep(1)
    if _temporal_cloud_compose_enabled() and _run_docker_compose_recreate_ui():
        for _ in range(15):
            if temporal_ui_up():
                return True
            time.sleep(1)
    return False


def _worker_state_path(repo: Path) -> Path:
    return resource_path(repo, ".tmp", "temporal", "worker-state.json")


def _read_worker_state(repo: Path) -> dict[str, Any] | None:
    path = _worker_state_path(repo)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _write_worker_state(
    repo: Path,
    *,
    review: str,
    repair: str,
    pid: int,
    overlay: Path,
    control_plane_code_identity: str | None = None,
) -> None:
    """Persist attestation for a repo-scoped Temporal worker process."""
    path = _worker_state_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    identity = control_plane_code_identity or compute_control_plane_code_identity()
    path.write_text(
        json.dumps(
            {
                "review": review,
                "repair": repair,
                "pid": pid,
                "overlay": str(overlay),
                "control_plane_code_identity": identity,
                "task_queue": TEMPORAL_TASK_QUEUE,
                "temporal_address": TEMPORAL_ADDRESS,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _worker_listening(pid: int, *, repo: Path | None = None) -> bool:
    if repo is not None:
        from worker_poll_ready import worker_is_polling

        if worker_is_polling(pid, repo):
            return True
    rows = _wmic_workers()
    if not any(row_pid == pid for row_pid, _ in rows):
        if repo is None:
            return False
        # WMIC may be unavailable; poll-ready marker is authoritative when repo is set.
        state = _read_worker_state(repo) or {}
        try:
            state_pid = int(state.get("pid"))
        except (TypeError, ValueError):
            state_pid = -1
        return state_pid == int(pid)
    if repo is None:
        return True
    from worker_poll_ready import worker_is_polling

    if worker_is_polling(pid, repo):
        return True
    # Worker, поднятый до poll-ready маркера: принимаем по attested worker-state.
    state = _read_worker_state(repo) or {}
    try:
        state_pid = int(state.get("pid"))
    except (TypeError, ValueError):
        state_pid = -1
    return state_pid == int(pid)


def _pause_worker_outcome(
    *,
    status: str,
    repo: Path,
    message: str,
    pid: int | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Pause payload for ``ensure_worker`` without mutating foreign processes."""

    payload: dict[str, Any] = {
        "action": "pause",
        "status": status,
        "message": message,
        "repo": str(repo.resolve()),
    }
    if pid is not None:
        payload["pid"] = pid
    if extra:
        payload.update(extra)
    return payload


def _worker_admission_pause(
    status: str,
    message: str,
    *,
    recovery_instructions: str,
    worker_pids: list[int] | None = None,
    pid: int | None = None,
) -> dict[str, Any]:
    """Admission pause when worker reuse would violate tier/identity invariants."""

    payload: dict[str, Any] = {
        "action": "pause",
        "status": status,
        "message": message,
        "recovery_instructions": recovery_instructions,
    }
    if worker_pids is not None:
        payload["worker_pids"] = worker_pids
    if pid is not None:
        payload["pid"] = pid
    return payload


def ensure_worker(
    repo: Path,
    overlay: Path,
    *,
    review: str,
    repair: str,
    timeout_s: int = 180,
) -> dict[str, Any]:
    """Reuse or start a repo-scoped worker; never kill foreign or ambiguous processes."""
    import time

    resolved_repo = repo.resolve()
    rows = list_worker_pids(repo=resolved_repo)
    attested = [(pid, cmd) for pid, cmd in rows if _worker_cmdline_matches_repo(cmd, resolved_repo)]
    if len(attested) != len(rows):
        rows = attested

    if len(rows) > 1:
        return _pause_worker_outcome(
            status="WORKER_AMBIGUOUS_MULTIPLE",
            repo=resolved_repo,
            message=(
                "Multiple worker.py processes match this repo; resolve manually "
                "(tempo_operator kill-workers --repo <path>) before starting a pipeline."
            ),
            extra={"pids": [pid for pid, _ in rows]},
        )

    state = _read_worker_state(resolved_repo)
    expected_identity = compute_control_plane_code_identity()
    tiers_match = (
        state is not None
        and state.get("review") == review
        and state.get("repair") == repair
        and state.get("overlay") == str(overlay)
    )

    if len(rows) > 1:
        return _worker_admission_pause(
            "WORKER_ADMISSION_AMBIGUOUS",
            f"Multiple Temporal worker processes detected ({len(rows)}).",
            recovery_instructions=(
                "Do not auto-kill workers. Confirm no active workflows, stop duplicate "
                "worker.py processes manually, then re-run admission."
            ),
            worker_pids=[pid for pid, _ in rows],
        )

    listening: list[tuple[int, str]] = [
        (pid, cmd) for pid, cmd in rows if _worker_listening(pid, repo=repo)
    ]

    if len(listening) == 1:
        pid, _cmd = listening[0]
        if not tiers_match:
            return _worker_admission_pause(
                "WORKER_TIER_MISMATCH",
                "Running worker overlay/tiers do not match the requested pipeline.",
                recovery_instructions=(
                    "Wait for in-flight workflows to finish, then restart worker.py with "
                    "the requested model overlay. Automatic worker replacement is forbidden."
                ),
                pid=pid,
                message="worker-state pid does not match the attested worker process.",
            )
        if not _worker_listening(pid):
            return _pause_worker_outcome(
                status="WORKER_NOT_LISTENING",
                repo=resolved_repo,
                pid=pid,
                message="Attested worker process is not listening; manual recovery required.",
            )
        recorded_identity = state.get("control_plane_code_identity") if state else None
        if not recorded_identity:
            return _pause_worker_outcome(
                status="WORKER_CODE_IDENTITY_UNVERIFIED",
                repo=resolved_repo,
                pid=pid,
                message="worker-state missing control_plane_code_identity attestation.",
            )
        if recorded_identity != expected_identity:
            return {
                "action": "pause",
                "status": "STALE_WORKER",
                "pid": pid,
                "message": "Worker control-plane identity is stale; restart worker explicitly.",
                "repo": str(resolved_repo),
            }
        if not tiers_match:
            return _pause_worker_outcome(
                status="WORKER_CONFIG_MISMATCH",
                repo=resolved_repo,
                pid=pid,
                message=(
                    "Running worker tiers/overlay do not match this pipeline request; "
                    "do not auto-replace while process is alive."
                ),
                extra={
                    "expected_review": review,
                    "expected_repair": repair,
                    "state_review": state.get("review") if state else None,
                    "state_repair": state.get("repair") if state else None,
                },
            )
        return {"action": "reused", "pid": pid}

    from worker_poll_ready import clear_poll_ready, repo_poll_ready_live_pid

    clear_poll_ready(resolved_repo)
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    repo_arg = str(resolved_repo).replace("\\", "/")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "worker.py",
            "--mode",
            "real",
            "--repo",
            repo_arg,
            "--model-config",
            str(overlay),
        ],
        cwd=str(HERE),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        # Windows: ``Popen.pid`` may be a launcher stub; poll-ready carries the real worker PID.
        ready_pid = repo_poll_ready_live_pid(resolved_repo)
        if ready_pid is not None:
            _write_worker_state(
                resolved_repo,
                review=review,
                repair=repair,
                pid=ready_pid,
                overlay=overlay,
                control_plane_code_identity=expected_identity,
            )
            return {"action": "started", "pid": ready_pid}
        if proc.poll() is not None:
            # Launcher stub may exit before poll-ready is written (Windows).
            for _ in range(5):
                ready_pid = repo_poll_ready_live_pid(resolved_repo)
                if ready_pid is not None:
                    break
                time.sleep(0.4)
            else:
                ready_pid = None
            if ready_pid is not None:
                _write_worker_state(
                    resolved_repo,
                    review=review,
                    repair=repair,
                    pid=ready_pid,
                    overlay=overlay,
                    control_plane_code_identity=expected_identity,
                )
                return {"action": "started", "pid": ready_pid}
            raise RuntimeError(f"worker.py exited early with code {proc.returncode}")
        time.sleep(1)
    proc.kill()
    raise RuntimeError("worker.py did not reach listening state in time")


def _cmd_run(args: argparse.Namespace) -> int:
    apply_cursor_sdk_bridge_temp_dir()
    repo = Path(args.repo)
    pipeline_id = args.pipeline
    if pipeline_id not in ("bugbot", "security", "project-bug-hunt", "verification"):
        raise SystemExit(
            f"run supports bugbot|security|project-bug-hunt|verification; got {pipeline_id!r}"
        )

    try:
        parsed, resolved, spec = _parse_run_request(
            pipeline_id,
            repo,
            review=args.review,
            repair=args.repair,
            change=args.change,
            base=args.base,
        )
    except ValueError as exc:
        _print_json({"status": "BLOCKED", "error": str(exc)})
        return 2

    review = parsed["review"]
    repair = parsed["repair"]
    overlay = write_overlay(repo, review, repair)

    lock = PipelineLock(
        pipeline_lock_path(repo, pipeline_id, change=resolved.get("change"))
    )
    if not lock.acquire():
        _print_json(
            {
                "status": "BLOCKED",
                "reason": "PIPELINE_ALREADY_RUNNING",
                "pipeline": pipeline_id,
                "message": (
                    "Identical pipeline is already running in another operator process. "
                    "Wait for it; do not start a second PP."
                ),
                **{k: v for k, v in resolved.items() if k in ("base", "head", "change")},
            }
        )
        return 2

    try:
        return _cmd_run_locked(
            repo=repo,
            pipeline_id=pipeline_id,
            resolved=resolved,
            spec=spec,
            review=review,
            repair=repair,
            overlay=overlay,
        )
    finally:
        lock.release()


def _cmd_run_locked(
    *,
    repo: Path,
    pipeline_id: str,
    resolved: dict[str, str],
    spec: dict[str, Any],
    review: str,
    repair: str,
    overlay: Path,
) -> int:
    if pipeline_id == "verification":
        change = resolved.get("change")
        if change:
            gate = evaluate_pre_run_gate(repo, change)
            if gate is not None:
                chat = gate.get("chat_instruction") or format_chat_instruction(
                    change=change, assignment=assignment_from_ledger(repo, change)
                )
                _print_json(
                    {
                        "status": "BLOCKED",
                        "reason": "OWNER_CHROME_FOCUS_GATE",
                        "pipeline": pipeline_id,
                        "change": change,
                        "message": gate.get("message"),
                        "chat_instruction": chat,
                        "gate_page_url": gate.get("gate_page_url"),
                        "manifest_path": gate.get("manifest_path"),
                        "resume_command": gate.get("resume_command"),
                        "ack_command": gate.get("ack_command"),
                        "go_ack_command": gate.get("go_ack_command"),
                        "browser_action": gate.get("browser_action"),
                        "required_check_id": gate.get("required_check_id"),
                        "assignment_epoch": gate.get("assignment_epoch"),
                        "owner_chrome_focus_gate": gate,
                        "next_action": (
                            "Owner: focus game Chrome tab, write GO in chat "
                            "(agent records go_ack), or run go_ack_command / ack_command, "
                            "then re-run /tempo-verification"
                        ),
                    }
                )
                return 2

    if not ensure_temporal_server(repair=True):
        _print_json(
            {
                "status": "BLOCKED",
                "reason": "TEMPORAL_SERVER_UNAVAILABLE",
                "message": "Start Docker Desktop, then rerun the slash command.",
            }
        )
        return 2

    try:
        worker = ensure_worker(repo, overlay, review=review, repair=repair)
    except RuntimeError as exc:
        _print_json({"status": "FAIL", "reason": "WORKER_START_FAILED", "message": str(exc)})
        return 1
    if worker.get("action") == "pause":
        _print_json(
            {
                "status": "BLOCKED",
                "reason": worker.get("status") or "WORKER_LIFECYCLE_BLOCKED",
                "message": worker.get("message"),
                "worker": worker,
                "pipeline": pipeline_id,
                **{k: v for k, v in resolved.items() if k in ("base", "head", "change")},
            }
        )
        return 2

    start_cmd = build_start_cmd(
        spec,
        repo=repo,
        overlay=overlay,
        review=review,
        repair=repair,
        resolved=resolved,
    )

    _print_json(
        {
            "status": "RUNNING",
            "pipeline": pipeline_id,
            "review": review,
            "repair": repair,
            **resolved,
            "overlay": str(overlay),
            "worker": worker,
            "temporal_ui": TEMPORAL_UI_URL,
        }
    )

    write_owner_monitoring_manifest(
        repo,
        build_monitoring_manifest(
            repo=repo,
            pipeline=pipeline_id,
            phase="PIPELINE_STARTING",
            resolved=resolved,
            review=review,
            repair=repair,
            temporal_ui=TEMPORAL_UI_URL,
        ),
    )

    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    proc = subprocess.Popen(
        start_cmd,
        cwd=str(HERE),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
    )
    stdout_lines: list[str] = []
    workflow_id: str | None = None
    assert proc.stdout is not None
    assert proc.stderr is not None
    while True:
        line = proc.stdout.readline()
        if line == "" and proc.poll() is not None:
            break
        if line:
            stdout_lines.append(line)
            if workflow_id is None and line.startswith("WORKFLOW_ID:"):
                workflow_id = line.split(":", 1)[1].strip()
                write_owner_monitoring_manifest(
                    repo,
                    build_monitoring_manifest(
                        repo=repo,
                        pipeline=pipeline_id,
                        phase="WORKFLOW_RUNNING",
                        resolved=resolved,
                        review=review,
                        repair=repair,
                        workflow_id=workflow_id,
                        temporal_ui=(
                            f"{TEMPORAL_UI_URL}/namespaces/default/workflows/{workflow_id}"
                        ),
                    ),
                    workflow_id=workflow_id,
                )
    return_code = proc.wait()
    stderr = proc.stderr.read() or ""
    stdout = "".join(stdout_lines)

    if workflow_id is None:
        for line in stdout.splitlines():
            if line.startswith("WORKFLOW_ID:"):
                workflow_id = line.split(":", 1)[1].strip()
                break

    already_running = return_code == 2 and "PIPELINE_ALREADY_RUNNING" in stdout
    if already_running:
        _print_json(
            {
                "status": "BLOCKED",
                "reason": "PIPELINE_ALREADY_RUNNING",
                "pipeline": pipeline_id,
                "workflow_id": workflow_id,
                "temporal_ui": (
                    f"{TEMPORAL_UI_URL}/namespaces/default/workflows/{workflow_id}"
                    if workflow_id
                    else TEMPORAL_UI_URL
                ),
                "message": (
                    "Identical pipeline is already RUNNING in Temporal. "
                    "Wait for it; do not start a second PP."
                ),
            }
        )
        return 2

    outcome_status = None
    review_runs = None
    repair_rounds = None
    review_evidence = None
    result_file = None
    browser_check_release = None
    if workflow_id:
        from run_store import durable_runs_dir

        runs_dir = durable_runs_dir(repo)
        candidate = runs_dir / f"{workflow_id}.json"
        if candidate.is_file():
            result_file = str(candidate)
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
                outcome_status = payload.get("status")
                review_runs = payload.get("review_runs")
                repair_rounds = payload.get("repair_rounds")
                review_evidence = payload.get("review_evidence")
                run_summary = payload.get("run_summary") or {}
                last_assigned = (
                    run_summary.get("last_assigned_check")
                    or payload.get("last_assigned_check")
                )
                if isinstance(last_assigned, dict):
                    browser_check_release = last_assigned.get("owner_browser_check_release")
            except json.JSONDecodeError:
                pass

    ok_statuses = set(spec.get("ok_statuses") or [])
    pipeline_ok = outcome_status in ok_statuses if outcome_status else False
    owner_focus_gate = outcome_status == "OWNER_CHROME_FOCUS_GATE"

    console_encoding_fail = (
        return_code != 0
        and "UnicodeEncodeError" in stderr
        and pipeline_ok
    )

    operator_status = "PASS" if pipeline_ok else "FAIL"
    if owner_focus_gate or outcome_status in (
        "VERIFICATION_CHECK_BLOCKED",
        "OWNER_CHROME_FOCUS_GATE",
    ):
        operator_status = "BLOCKED"
    post_completion = run_post_completion_steps(
        repo=repo,
        pipeline_id=pipeline_id,
        spec=spec,
        workflow_id=workflow_id,
        outcome=outcome_status,
        resolved=resolved,
        operator_status=operator_status,
    )

    final = {
        "status": operator_status,
        "pipeline": pipeline_id,
        "workflow_id": workflow_id,
        "review": review,
        "repair": repair,
        **resolved,
        "worker": worker,
        "outcome": outcome_status,
        "review_runs": review_runs,
        "repair_rounds": repair_rounds,
        "result_file": result_file,
        "temporal_ui": (
            f"{TEMPORAL_UI_URL}/namespaces/default/workflows/{workflow_id}"
            if workflow_id
            else TEMPORAL_UI_URL
        ),
        "process_note": "console_encoding_only" if console_encoding_fail else None,
        "post_completion": post_completion,
        "done_page_url": (
            post_completion[0].get("done_page_url")
            if post_completion and not post_completion[0].get("skipped")
            else None
        ),
        "browser_check_release": browser_check_release,
        "browser_check_release_page_url": (
            browser_check_release.get("release_page_url")
            if isinstance(browser_check_release, dict)
            and not browser_check_release.get("skipped")
            else None
        ),
        "browser_check_release_chat_message": (
            browser_check_release.get("chat_message")
            if isinstance(browser_check_release, dict)
            and not browser_check_release.get("skipped")
            else None
        ),
    }
    if workflow_id:
        dump_payload: dict[str, Any] = {
            "run_summary": final,
            "stdout_tail": stdout[-4000:],
            "stderr_tail": stderr[-2000:],
        }
        if review_evidence is not None:
            dump_payload["review_evidence"] = review_evidence
        dump_result_file(workflow_id, dump_payload, repo)
        write_owner_monitoring_manifest(
            repo,
            build_monitoring_manifest(
                repo=repo,
                pipeline=pipeline_id,
                phase="COMPLETE",
                resolved=resolved,
                review=review,
                repair=repair,
                workflow_id=workflow_id,
                operator_status=operator_status,
                outcome=outcome_status,
                result_file=result_file,
                temporal_ui=final.get("temporal_ui"),
            ),
            workflow_id=workflow_id,
        )
        art = artifacts_monitoring_path()
        if art.is_file():
            final["owner_monitoring_artifact"] = str(art)
        latest_mon = resource_path(repo, "opsx", "runs", "temporal", "monitoring", "latest.json")
        if latest_mon.is_file():
            final["owner_monitoring_manifest"] = str(latest_mon)
        if pipeline_ok:
            try:
                from adversarial_closure import write_adversarial_ack
                from model_config import AgentModelConfig

                change_for_ack = resolved.get("change") or resolved.get(
                    "adversarial_change_id"
                )
                if change_for_ack and outcome_status:
                    model_summary = AgentModelConfig.load(overlay).summary()
                    write_adversarial_ack(
                        repo=repo,
                        change_id=str(change_for_ack),
                        pipeline=pipeline_id,
                        review_tier=review,
                        repair_tier=repair,
                        outcome=str(outcome_status),
                        run_summary=final,
                        model_summary=model_summary,
                    )
            except Exception as exc:
                final["adversarial_ack_error"] = str(exc)
    _print_json(final)
    return 0 if pipeline_ok or console_encoding_fail else 1


def write_overlay(repo: Path, review: str, repair: str) -> Path:
    dest = overlay_path(repo)
    write_step_overlay(
        review=review,
        repair=repair,
        dest=dest,
        source=DEFAULT_MODEL_CONFIG_PATH,
    )
    return dest


def temporal_ui_up() -> bool:
    """UI готов, когда workflow list API отвечает (SPA 200 без gRPC даёт ложный UP)."""
    import urllib.request

    api_url = (
        f"{TEMPORAL_UI_URL.rstrip('/')}/api/v1/namespaces/default/workflows?query="
    )
    try:
        with urllib.request.urlopen(api_url, timeout=5) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except OSError:
        return False


def _ps_workers() -> list[tuple[int, str]]:
    """POSIX worker enumeration for cloud/Linux operator hosts."""
    try:
        raw = subprocess.check_output(
            ["ps", "-eo", "pid=,args="],
            stderr=subprocess.DEVNULL,
            text=True,
            errors="replace",
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    found: list[tuple[int, str]] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or "worker.py" not in stripped:
            continue
        if " ps " in f" {stripped} " or "grep" in stripped:
            continue
        parts = stripped.split(None, 1)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        found.append((pid, parts[1]))
    return found


def _wmic_workers() -> list[tuple[int, str]]:
    if sys.platform != "win32":
        return _ps_workers()
    try:
        raw = subprocess.check_output(
            [
                "wmic",
                "process",
                "where",
                "CommandLine like '%worker.py%'",
                "get",
                "ProcessId,CommandLine",
                "/FORMAT:LIST",
            ],
            stderr=subprocess.DEVNULL,
            text=True,
            errors="replace",
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    pid: int | None = None
    cmd = ""
    found: list[tuple[int, str]] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("CommandLine="):
            cmd = stripped.split("=", 1)[1]
        elif stripped.startswith("ProcessId="):
            try:
                pid = int(stripped.split("=", 1)[1])
            except ValueError:
                pid = None
        if pid is not None and cmd:
            if "worker.py" in cmd and "wmic" not in cmd.lower():
                found.append((pid, cmd))
            pid = None
            cmd = ""
    return found


def _worker_cmdline_matches_repo(cmdline: str, repo: Path) -> bool:
    """True when ``worker.py --repo …`` cmdline targets this workspace."""

    resolved = repo.resolve()
    hay = cmdline.replace("\\", "/").casefold()
    for variant in (
        str(resolved),
        str(resolved).replace("\\", "/"),
        resolved.as_posix(),
    ):
        needle = variant.replace("\\", "/").casefold()
        if needle and needle in hay:
            return True
    return False


def list_worker_pids(*, repo: Path | None = None) -> list[tuple[int, str]]:
    """Enumerate worker.py processes; when ``repo`` is set, only attested repo workers."""
    rows = _wmic_workers()
    if repo is None:
        return rows
    resolved = repo.resolve()
    matched: list[tuple[int, str]] = []
    for pid, cmd in rows:
        if _worker_cmdline_matches_repo(cmd, resolved):
            matched.append((pid, cmd))
    return matched


def kill_workers(*, repo: Path | None = None) -> list[int]:
    """Terminate worker.py processes attested to ``repo`` only (explicit operator action)."""
    import signal

    if repo is None:
        return []
    killed: list[int] = []
    for pid, _cmd in list_worker_pids(repo=repo):
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        killed.append(pid)
    return killed


def _activity_name(event: Any) -> str:
    attrs = getattr(event, "activity_task_scheduled_event_attributes", None)
    if attrs and getattr(attrs, "activity_type", None):
        return attrs.activity_type.name
    return ""


def _decode_payload(event: Any) -> dict[str, Any] | None:
    attrs = getattr(event, "activity_task_completed_event_attributes", None)
    if not attrs or not attrs.result or not attrs.result.payloads:
        return None
    raw = attrs.result.payloads[0].data.decode("utf-8", "replace")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _summarize_result(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"activity": name}
    for key in (
        "clean",
        "completed",
        "fingerprint",
        "bug_batch_clear",
        "uncertain",
        "repaired_this_batch",
        "session_id",
        "git_preflight_ok",
    ):
        if key in payload:
            out[key] = payload[key]
    for key in ("repairable_findings", "excluded_findings", "findings"):
        if isinstance(payload.get(key), list):
            out[f"{key}_count"] = len(payload[key])
    return out


async def describe_workflow(workflow_id: str) -> dict[str, Any]:
    from temporal_client import connect_temporal_client

    client = await connect_temporal_client()
    handle = client.get_workflow_handle(workflow_id)
    desc = await handle.describe()
    hist = await handle.fetch_history()
    trail: list[str] = []
    completed: list[dict[str, Any]] = []
    pending: str | None = None
    failed: list[str] = []
    current: str | None = None
    for event in hist.events:
        et = event.event_type
        if et == EVENT_ACTIVITY_SCHEDULED:
            current = _activity_name(event)
            trail.append(current)
            pending = current
        elif et == EVENT_ACTIVITY_COMPLETED and current:
            payload = _decode_payload(event)
            if payload:
                completed.append(_summarize_result(current, payload))
            pending = None
            current = None
        elif et == EVENT_ACTIVITY_FAILED and current:
            failed.append(current)
            pending = None
            current = None
    result = None
    status_name = desc.status.name if hasattr(desc.status, "name") else str(desc.status)
    if status_name == "COMPLETED":
        try:
            result = await handle.result()
        except Exception as exc:  # noqa: BLE001
            result = {"status": "RESULT_UNAVAILABLE", "message": str(exc)}
    compact = None
    if isinstance(result, dict):
        compact = {
            "status": result.get("status"),
            "repair_rounds": result.get("repair_rounds"),
            "review_runs": result.get("review_runs"),
            "message": result.get("message"),
            "session_id": result.get("session_id"),
        }
    return {
        "workflow_id": workflow_id,
        "status": status_name,
        "run_id": getattr(desc, "run_id", None),
        "ui": f"{TEMPORAL_UI_URL}/namespaces/default/workflows/{workflow_id}",
        "queue": TEMPORAL_TASK_QUEUE,
        "trail": trail,
        "pending": pending,
        "failed_activities": failed,
        "completed_summaries": completed,
        "result": compact,
    }


def dump_result_file(workflow_id: str, payload: dict[str, Any], repo: Path) -> Path:
    from run_store import durable_runs_dir

    dest = durable_runs_dir(repo) / f"{workflow_id}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return dest


def _print_json(payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    try:
        print(text)
    except UnicodeEncodeError:
        print(json.dumps(payload, ensure_ascii=True, indent=2))


def _cmd_pipeline(args: argparse.Namespace) -> int:
    spec = get_pipeline(args.pipeline)
    _print_json(spec)
    return 0


def _cmd_overlay(args: argparse.Namespace) -> int:
    review, repair = parse_tier_pair(args.review, args.repair)
    dest = write_overlay(Path(args.repo), review, repair)
    _print_json(
        {
            "overlay": str(dest),
            "review": review,
            "repair": repair,
            "tiers": list(ALLOWED_TIERS),
        }
    )
    return 0


def _cmd_ensure_temporal(args: argparse.Namespace) -> int:
    if ensure_temporal_server(repair=args.repair):
        _print_json({"temporal": "UP", "address": TEMPORAL_ADDRESS, "ui": TEMPORAL_UI_URL})
        return 0
    _print_json(
        {
            "temporal": "DOWN",
            "address": TEMPORAL_ADDRESS,
            "ui": TEMPORAL_UI_URL,
            "how_to_start": (
                "cd tools/temporal && "
                "STROY_TEMPORAL_CLOUD_COMPOSE=1 docker compose "
                "-f docker-compose.yml -f docker-compose.cloud.yml up -d"
            ),
        }
    )
    return 2


def _cmd_workers(args: argparse.Namespace) -> int:
    repo = Path(args.repo)
    rows = list_worker_pids(repo=repo)
    _print_json({"workers": [{"pid": pid, "cmd": cmd[:200]} for pid, cmd in rows]})
    return 0


def _cmd_kill_workers(args: argparse.Namespace) -> int:
    killed = kill_workers(repo=Path(args.repo))
    _print_json({"killed": killed})
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    import asyncio

    from run_store import durable_runs_dir

    repo = Path(args.repo)
    payload = asyncio.run(describe_workflow(args.workflow_id))
    dump_result_file(args.workflow_id, payload, repo)
    write_owner_monitoring_manifest(
        repo,
        build_monitoring_manifest(
            repo=repo,
            pipeline="unknown",
            phase="STATUS_POLL",
            resolved={},
            review="",
            repair="",
            workflow_id=args.workflow_id,
            outcome=str(payload.get("status")),
            result_file=str(durable_runs_dir(repo) / f"{args.workflow_id}.json"),
            temporal_ui=str(payload.get("ui")),
            extra={"trail": payload.get("trail"), "pending": payload.get("pending")},
        ),
        workflow_id=args.workflow_id,
    )
    _print_json(payload)
    return 0 if payload.get("status") != "FAILED" else 1


def _cmd_owner_done_signal(args: argparse.Namespace) -> int:
    repo = Path(args.repo)
    spec = get_pipeline(args.pipeline)
    resolved: dict[str, str] = {}
    if args.change:
        resolved["change"] = args.change
    payload = run_post_completion_steps(
        repo=repo,
        pipeline_id=args.pipeline,
        spec=spec,
        workflow_id=args.workflow_id,
        outcome=args.outcome,
        resolved=resolved,
        operator_status=args.operator_status,
    )
    _print_json({"post_completion": payload, "done_page_url": done_page_file_url(repo=repo)})
    return 0


def _cmd_owner_chrome_focus_ack(args: argparse.Namespace) -> int:
    repo = Path(args.repo)
    change = (args.change or "").strip()
    if not change:
        _print_json({"error": "change= is required"})
        return 2
    assignment = assignment_from_ledger(repo, change)
    if assignment is None:
        _print_json({"error": "no current_assignment in ledger", "change": change})
        return 2
    required_check_id = (args.required_check_id or assignment.get("required_check_id") or "").strip()
    epoch = args.assignment_epoch
    if epoch is None:
        epoch = int(assignment.get("assignment_epoch") or 0)
    if not required_check_id or epoch <= 0:
        _print_json({"error": "required_check_id and assignment_epoch required"})
        return 2
    path = write_focus_ack(
        repo,
        required_check_id=required_check_id,
        assignment_epoch=epoch,
        change=change,
    )
    _print_json(
        {
            "step_id": FOCUS_GATE_STEP_ID,
            "ack_path": str(path),
            "required_check_id": required_check_id,
            "assignment_epoch": epoch,
            "change": change,
            "resume_command": f"/tempo-verification change={change} low low",
        }
    )
    return 0


def _cmd_owner_chrome_focus_gate(args: argparse.Namespace) -> int:
    repo = Path(args.repo)
    change = (args.change or "").strip()
    if not change:
        _print_json({"error": "change= is required"})
        return 2
    gate = evaluate_pre_run_gate(repo, change)
    if gate is None:
        _print_json({"step_id": FOCUS_GATE_STEP_ID, "skipped": True, "reason": "not_required"})
        return 0
    _print_json({"owner_chrome_focus_gate": gate, "gate_page_url": gate.get("gate_page_url")})
    return 2


def _cmd_owner_browser_check_go(args: argparse.Namespace) -> int:
    repo = Path(args.repo)
    change = (args.change or "").strip()
    if not change:
        _print_json({"error": "change= is required"})
        return 2
    assignment = assignment_from_ledger(repo, change)
    if assignment is None:
        _print_json({"error": "no current_assignment in ledger", "change": change})
        return 2
    required_check_id = (args.required_check_id or assignment.get("required_check_id") or "").strip()
    epoch = args.assignment_epoch
    if epoch is None:
        epoch = int(assignment.get("assignment_epoch") or 0)
    if not required_check_id or epoch <= 0:
        _print_json({"error": "required_check_id and assignment_epoch required"})
        return 2
    path = write_go_ack(
        repo,
        required_check_id=required_check_id,
        assignment_epoch=epoch,
        change=change,
        source=args.source,
    )
    _print_json(
        {
            "step_id": GO_STEP_ID,
            "ack_path": str(path),
            "required_check_id": required_check_id,
            "assignment_epoch": epoch,
            "change": change,
            "resume_command": f"/tempo-verification change={change} low low",
        }
    )
    return 0


def _cmd_owner_browser_check_release(args: argparse.Namespace) -> int:
    repo = Path(args.repo)
    change = (args.change or "").strip()
    if not change:
        _print_json({"error": "change= is required"})
        return 2
    assignment = assignment_from_ledger(repo, change)
    if assignment is None:
        _print_json({"error": "no current_assignment in ledger", "change": change})
        return 2
    result = run_browser_check_release(
        repo=repo,
        change=change,
        assignment=assignment,
        durable_result_status=args.durable_status,
    )
    _print_json({"owner_browser_check_release": result})
    return 0 if not result.get("skipped") else 0


def _cmd_owner_browser_check_instruction(args: argparse.Namespace) -> int:
    repo = Path(args.repo)
    change = (args.change or "").strip()
    if not change:
        _print_json({"error": "change= is required"})
        return 2
    assignment = assignment_from_ledger(repo, change)
    chat = format_chat_instruction(change=change, assignment=assignment)
    _print_json({"chat_instruction": chat, "release_page_url": release_page_file_url(repo=repo)})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Process operator for /tempo-* pipelines")
    parser.add_argument("--repo", default=str(ROOT))
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_pipe = sub.add_parser("pipeline", help="Print a pipeline spec")
    p_pipe.add_argument("pipeline")
    p_pipe.set_defaults(func=_cmd_pipeline)

    p_over = sub.add_parser("overlay", help="Write review/repair model overlay")
    p_over.add_argument("--review", required=True)
    p_over.add_argument("--repair", required=True)
    p_over.set_defaults(func=_cmd_overlay)

    p_tmp = sub.add_parser("ensure-temporal", help="Check Temporal; optionally docker compose up")
    p_tmp.add_argument("--repair", action="store_true")
    p_tmp.set_defaults(func=_cmd_ensure_temporal)

    p_w = sub.add_parser("workers", help="List worker.py processes")
    p_w.set_defaults(func=_cmd_workers)

    p_k = sub.add_parser("kill-workers", help="Kill worker.py processes for this repo")
    p_k.set_defaults(func=_cmd_kill_workers)

    p_s = sub.add_parser("status", help="Describe a workflow (ASCII-safe JSON)")
    p_s.add_argument("--workflow-id", required=True)
    p_s.set_defaults(func=_cmd_status)

    p_r = sub.add_parser("resolve-args", help="Resolve pipeline CLI tokens to base/head/change")
    p_r.add_argument("pipeline")
    p_r.add_argument("--review", required=True)
    p_r.add_argument("--repair", required=True)
    p_r.add_argument("--change", default=None)
    p_r.add_argument("--base", default=None)
    p_r.set_defaults(func=_cmd_resolve_args)

    p_run = sub.add_parser(
        "run",
        help="Full operator: Temporal + worker + workflow (Owner uses slash command only)",
    )
    p_run.add_argument("pipeline")
    p_run.add_argument("--review", required=True)
    p_run.add_argument("--repair", required=True)
    p_run.add_argument("--change", default=None)
    p_run.add_argument("--base", default=None)
    p_run.set_defaults(func=_cmd_run)

    p_done = sub.add_parser(
        "owner-done-signal",
        help="Run PP owner-done-signal post-completion step (manifest + file URL)",
    )
    p_done.add_argument("pipeline", choices=["verification"])
    p_done.add_argument("--workflow-id", default=None)
    p_done.add_argument("--outcome", required=True)
    p_done.add_argument("--change", default=None)
    p_done.add_argument(
        "--operator-status",
        default=None,
        choices=["PASS", "FAIL", "BLOCKED", "STOP"],
    )
    p_done.set_defaults(func=_cmd_owner_done_signal)

    p_focus = sub.add_parser(
        "owner-chrome-focus-gate",
        help="Emit owner-chrome-focus-gate manifest when smoke check is next (no workflow)",
    )
    p_focus.add_argument("pipeline", choices=["verification"])
    p_focus.add_argument("--change", required=True)
    p_focus.set_defaults(func=_cmd_owner_chrome_focus_gate)

    p_focus_ack = sub.add_parser(
        "owner-chrome-focus-ack",
        help="Acknowledge Owner focused game Chrome for current smoke assignment",
    )
    p_focus_ack.add_argument("pipeline", choices=["verification"])
    p_focus_ack.add_argument("--change", required=True)
    p_focus_ack.add_argument("--required-check-id", default=None)
    p_focus_ack.add_argument("--assignment-epoch", type=int, default=None)
    p_focus_ack.set_defaults(func=_cmd_owner_chrome_focus_ack)

    p_bc_go = sub.add_parser(
        "owner-browser-check-go",
        help="Record Owner GO ack for current smoke assignment (chat or CLI)",
    )
    p_bc_go.add_argument("pipeline", choices=["verification"])
    p_bc_go.add_argument("--change", required=True)
    p_bc_go.add_argument("--required-check-id", default=None)
    p_bc_go.add_argument("--assignment-epoch", type=int, default=None)
    p_bc_go.add_argument("--source", default="cli", help="cli | owner_chat")
    p_bc_go.set_defaults(func=_cmd_owner_browser_check_go)

    p_bc_rel = sub.add_parser(
        "owner-browser-check-release",
        help="Emit owner-browser-check-release manifest after smoke executor",
    )
    p_bc_rel.add_argument("pipeline", choices=["verification"])
    p_bc_rel.add_argument("--change", required=True)
    p_bc_rel.add_argument("--durable-status", default=None)
    p_bc_rel.set_defaults(func=_cmd_owner_browser_check_release)

    p_bc_inst = sub.add_parser(
        "owner-browser-check-instruction",
        help="Print Owner chat instruction template for imminent browser smoke",
    )
    p_bc_inst.add_argument("pipeline", choices=["verification"])
    p_bc_inst.add_argument("--change", required=True)
    p_bc_inst.set_defaults(func=_cmd_owner_browser_check_instruction)
    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    os.environ.setdefault("PYTHONUTF8", "1")
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
