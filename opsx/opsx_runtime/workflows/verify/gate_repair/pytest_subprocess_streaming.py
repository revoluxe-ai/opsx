"""Потоковый subprocess pytest: stall/suite-timeout — finding; сторож без logging."""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from workflows.implementation.node_subprocess import decode_subprocess_output, subprocess_utf8_env
from agents.verify.test_duration.stall_function_analysis import (
    stall_function_analysis_from_stderr,
)
from workflows.verify.gate_repair.pytest_gate_timeout import (
    QUALITY_GATE_PER_TEST_SECONDS,
    QUALITY_GATE_PYTEST_STALL_LIMIT_SECONDS,
    format_pytest_stall_evidence,
    format_pytest_timeout_evidence,
)
from workflows.verify.gate_repair.pytest_opsx_gate_progress_parse import (
    iter_opsx_gate_segments,
)
from workflows.verify.gate_repair.schemas import GateCheckResult

log = logging.getLogger("opsx.verify.routing")

_STDIO_TAIL_CHARS = 3_500
_UNKNOWN_NODE = "UNKNOWN_NODE"
_PYTEST_NODE_LINE = re.compile(r"^([\w./\\:-]+\.py::\S+)")
_TERMINAL_STATUS_RE = re.compile(r"\b(PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)\b")
_COLLECTED_SUMMARY_RE = re.compile(r"\b\d+\s+tests?\s+collected\b", re.IGNORECASE)
_HEARTBEAT_INTERVAL_S = 30.0
_TASKKILL_TIMEOUT_S = 15.0
_TASKKILL_MAX_ATTEMPTS = 3
# Те же 15 с, что и product call: тихий subprocess внутри теста не получает отдельный бюджет.
_SUBPROCESS_HARNESS_CALL_LIMIT_S = 15.0
_SUBPROCESS_HARNESS_NODE_RE = re.compile(
    r"test_pytest_collection_scope\.py::test_(?:broad_pytest_dot|targeted_product)_"
)
# Smoke с bootstrap subprocess (openspec setup.sh, poetry toolchain): в call нет вывода pytest.
_LONG_RUNNING_SMOKE_CALL_LIMIT_S = 240.0
_LONG_RUNNING_SMOKE_NODE_RE = re.compile(
    r"tests/smoke/test_(?:openspec_cli_provider_smoke|poetry_dev_toolchain_smoke)\.py::"
)
# Юнит-тесты, поднимающие дочерний ``python`` без stdout в pytest (suite load).
_SUBPROCESS_SPAWN_CALL_LIMIT_S = 90.0
_SUBPROCESS_SPAWN_NODE_RE = re.compile(
    r"(?:test_validate_injected_db_urls|test_opsx_continue_skill_contract)\.py::"
)
_CALL_STALL_STACK_GRACE_S = 0.75
_STACK_TRACE_MARKERS = (
    "Stack (most recent call last)",
    "Timeout (>=",
    "File ",
    "  File ",
)
PytestPhase = str  # collect | running | result | setup | call | teardown


@dataclass(frozen=True, slots=True)
class _LogWorkItem:
    """Задание для потока логирования — reader только ставит в очередь."""

    kind: Literal["progress", "heartbeat"]
    scope: str
    phase: str
    node_id: str
    silence_s: int | None = None


def _tail(text: str, limit: int = _STDIO_TAIL_CHARS) -> str:
    clean = text.strip()
    if len(clean) <= limit:
        return clean
    return f"… [truncated; showing last {limit} chars]\n{clean[-limit:]}"


def _combine_stdio(stdout: str, stderr: str) -> str:
    parts = [stdout.strip(), stderr.strip()]
    return "\n".join(part for part in parts if part)


def argv_with_progress_verbosity(command: list[str]) -> list[str]:
    """Заменить ``-q`` на ``-v`` для видимого хода pytest без смены остального argv."""
    if "--collect-only" in command:
        return list(command)
    out: list[str] = []
    replaced_quiet = False
    for arg in command:
        if arg == "-q":
            out.append("-v")
            replaced_quiet = True
        else:
            out.append(arg)
    if not replaced_quiet and "-v" not in out:
        out.append("-v")
    return out


def _gate_pytest_env(cwd: Path, extra_env: dict[str, str] | None = None) -> dict[str, str]:
    """Окружение subprocess pytest: UTF-8 stdio и ``opsx/opsx_runtime`` на ``PYTHONPATH``."""
    env = subprocess_utf8_env({"PYTHONUNBUFFERED": "1"})
    runtime = (cwd / "opsx" / "opsx_runtime").resolve()
    if runtime.is_dir():
        sep = os.pathsep
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{runtime}{sep}{existing}" if existing else str(runtime)
    if extra_env:
        env.update(extra_env)
    return env


def _extract_node_id(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("="):
        return None
    match = _PYTEST_NODE_LINE.match(stripped)
    if match:
        return match.group(1)
    if ".py::" in stripped:
        token = stripped.split()[0]
        if ".py::" in token:
            return token
    return None


def _parse_opsx_gate_line(line: str) -> dict[str, str] | None:
    """Последний сегмент OPSX в строке (pytest может склеить ``PASSED`` и progress)."""
    last: dict[str, str] | None = None
    for segment in iter_opsx_gate_segments(line):
        last = segment
    return last


def _classify_pytest_line(line: str) -> tuple[str | None, PytestPhase | None, bool]:
    """Классификация строки: node, фаза, считается ли прогрессом pytest."""
    segments = list(iter_opsx_gate_segments(line))
    if segments:
        gate = segments[-1]
        return gate["node"], gate["when"], True
    stripped = line.strip()
    lowered = stripped.lower()
    node = _extract_node_id(line)
    if node and _TERMINAL_STATUS_RE.search(stripped):
        return node, "result", True
    if node:
        return node, "running", True
    if _COLLECTED_SUMMARY_RE.search(stripped) or (
        "collected" in lowered and "item" in lowered
    ):
        return None, "collect", True
    if lowered.startswith("<function") or lowered.startswith("<module"):
        return None, "collect", True
    if "collecting" in lowered and "..." in lowered:
        return None, "collect", True
    if stripped.startswith("=") and ("test session" in lowered or "passed" in lowered):
        return None, "collect", True
    return None, None, False


def is_pytest_progress_line(line: str) -> bool:
    """Публичный предикат: строка сбрасывает stall-таймер (только pytest-прогресс)."""
    return _classify_pytest_line(line)[2]


def _process_tree_alive(proc: subprocess.Popen[bytes]) -> bool:
    return proc.poll() is None


def _kill_process_tree(proc: subprocess.Popen[bytes]) -> bool:
    """Убить дерево subprocess. ``True`` — процесс завершён."""
    if proc.poll() is not None:
        return True
    for _attempt in range(_TASKKILL_MAX_ATTEMPTS):
        if sys.platform == "win32":
            _ = subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=_TASKKILL_TIMEOUT_S,
            )
        else:
            proc.kill()
        try:
            proc.wait(timeout=_TASKKILL_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            pass
        if proc.poll() is not None:
            return True
    return proc.poll() is not None


@dataclass
class _StreamWatchState:
    """Состояние reader'ов; сторож читает снимки без вызова logging."""

    lock: threading.Lock = field(default_factory=threading.Lock)
    last_progress_at: float = 0.0
    last_node_id: str = _UNKNOWN_NODE
    last_running_node_id: str = _UNKNOWN_NODE
    last_logged_phase: PytestPhase | None = None
    last_logged_node: str | None = None
    last_heartbeat_log: float = 0.0
    active_when: str | None = None
    active_node_id: str = _UNKNOWN_NODE
    call_phase_started_at: float | None = None
    call_phase_open: bool = False
    call_phase_limit_effective: float = 0.0
    stall_when: str = "unknown"
    hang_product_eligible: bool = False
    stalled: bool = False
    timed_out: bool = False
    kill_failed: bool = False
    stack_trace_seen: bool = False
    call_stall_pending: bool = False
    call_stall_kill_at: float = 0.0
    effective_call_limit_s: float = 0.0
    stop_watchdog: threading.Event = field(default_factory=threading.Event)
    stop_log_worker: threading.Event = field(default_factory=threading.Event)


def _is_long_running_smoke_node(node: str) -> bool:
    """Smoke с тихим bootstrap subprocess в фазе call (нет строк pytest)."""
    normalized = node.replace("\\", "/")
    return bool(_LONG_RUNNING_SMOKE_NODE_RE.search(normalized))


def _is_subprocess_spawn_unit_node(node: str) -> bool:
    """Тесты с тихим subprocess (preflight/guard) в call без строк pytest под suite load."""
    normalized = node.replace("\\", "/")
    return bool(_SUBPROCESS_SPAWN_NODE_RE.search(normalized))


def _effective_call_limit_for_node(node: str, default_limit: float) -> float:
    """Лимит фазы ``call``: harness с ``subprocess.run(collect)`` — дольше 15 с."""
    if _is_long_running_smoke_node(node):
        return _LONG_RUNNING_SMOKE_CALL_LIMIT_S
    if _is_subprocess_spawn_unit_node(node):
        return _SUBPROCESS_SPAWN_CALL_LIMIT_S
    if _SUBPROCESS_HARNESS_NODE_RE.search(node):
        return _SUBPROCESS_HARNESS_CALL_LIMIT_S
    return default_limit


def _hang_product_eligible_for_call_stall(node: str) -> bool:
    """``HANG_PRODUCT`` только для product call, не для harness subprocess-тестов."""
    normalized = node.replace("\\", "/")
    if _is_long_running_smoke_node(normalized):
        return False
    if _is_subprocess_spawn_unit_node(normalized):
        return False
    return not bool(_SUBPROCESS_HARNESS_NODE_RE.search(node))


def _apply_gate_event(
    state: _StreamWatchState,
    gate: dict[str, str],
    now: float,
    *,
    default_call_limit: float,
) -> None:
    """Обновить дедлайны по машинной строке OPSX (под lock)."""
    node = gate["node"]
    when = gate["when"]
    state.last_progress_at = now
    state.last_node_id = node
    state.active_when = when
    state.active_node_id = node
    if gate["event"] == "start":
        if when == "call":
            state.call_phase_started_at = now
            state.call_phase_open = True
            raw_limit = gate.get("call_limit_s")
            if raw_limit:
                try:
                    state.call_phase_limit_effective = float(raw_limit)
                except ValueError:
                    state.call_phase_limit_effective = _effective_call_limit_for_node(
                        node, default_call_limit
                    )
            else:
                state.call_phase_limit_effective = _effective_call_limit_for_node(
                    node, default_call_limit
                )
            state.effective_call_limit_s = state.call_phase_limit_effective
            state.last_running_node_id = node
        return
    if gate["event"] == "report":
        outcome = gate["outcome"]
        if when == "call":
            raw_limit = gate.get("call_limit_s")
            if raw_limit and state.call_phase_open:
                try:
                    parsed = float(raw_limit)
                    if parsed > state.call_phase_limit_effective:
                        state.call_phase_limit_effective = parsed
                        state.effective_call_limit_s = parsed
                except ValueError:
                    pass
            state.call_phase_open = False
            state.call_phase_started_at = None
        if outcome == "skipped" and when == "call":
            state.last_running_node_id = _UNKNOWN_NODE


def _apply_legacy_line(state: _StreamWatchState, node: str | None, phase: PytestPhase | None, now: float) -> None:
    """Fallback по ``-v``, если плагин не загружен."""
    state.last_progress_at = now
    if node:
        state.last_node_id = node
        if phase == "result":
            state.call_phase_open = False
            state.call_phase_started_at = None
            state.active_when = None
        elif phase == "running":
            state.last_running_node_id = node
            state.active_when = None


def _snapshot_for_watchdog(
    state: _StreamWatchState,
) -> tuple[float, float | None, bool, str, float, float, str | None]:
    """Короткий снимок под lock (сторож не вызывает logging)."""
    with state.lock:
        return (
            state.last_progress_at,
            state.call_phase_started_at,
            state.call_phase_open,
            state.active_node_id,
            state.last_progress_at,
            state.call_phase_limit_effective,
            state.active_when,
        )


def _watchdog_loop(
    proc: subprocess.Popen[bytes],
    state: _StreamWatchState,
    *,
    started: float,
    suite_timeout_s: float | None,
    stall_limit_s: float,
    call_limit_s: float,
) -> None:
    """Suite backstop, тишина stall_limit и call без итога — без ``logging``."""
    last_watchdog_pulse = started
    while not state.stop_watchdog.is_set():
        if proc.poll() is not None:
            state.stop_watchdog.set()
            return
        now = time.monotonic()
        (
            last_progress_at,
            call_started_at,
            call_open,
            active_node,
            _,
            call_limit_effective,
            active_when,
        ) = _snapshot_for_watchdog(state)
        long_smoke = _is_long_running_smoke_node(active_node)
        spawn_unit = _is_subprocess_spawn_unit_node(active_node)
        quiet_subprocess_phase = (long_smoke or spawn_unit) and active_when in {
            "setup",
            "call",
        }
        extended_call_limit = (
            _LONG_RUNNING_SMOKE_CALL_LIMIT_S
            if long_smoke
            else _SUBPROCESS_SPAWN_CALL_LIMIT_S
            if spawn_unit
            else 0.0
        )
        if quiet_subprocess_phase and extended_call_limit > 0.0 and not call_open:
            with state.lock:
                state.call_phase_open = True
                if state.call_phase_started_at is None:
                    state.call_phase_started_at = now
                if state.call_phase_limit_effective < extended_call_limit:
                    state.call_phase_limit_effective = extended_call_limit
                    state.effective_call_limit_s = extended_call_limit
            call_open = True
            call_started_at = call_started_at or now
            call_limit_effective = max(call_limit_effective, extended_call_limit)
        if (
            quiet_subprocess_phase
            and now - last_watchdog_pulse >= _HEARTBEAT_INTERVAL_S
        ):
            with state.lock:
                state.last_progress_at = now
            last_watchdog_pulse = now
        call_limit = call_limit_effective if call_limit_effective > 0.0 else call_limit_s
        progress_silent = now - last_progress_at
        if progress_silent >= stall_limit_s:
            state.stalled = True
            state.stall_when = "silence"
            state.hang_product_eligible = False
            if not _kill_process_tree(proc):
                state.kill_failed = True
            state.stop_watchdog.set()
            return
        with state.lock:
            call_stall_pending = state.call_stall_pending
            call_stall_kill_at = state.call_stall_kill_at
        if call_open and call_started_at is not None:
            call_elapsed = now - call_started_at
            if not call_stall_pending and call_elapsed >= call_limit:
                with state.lock:
                    state.call_stall_pending = True
                    state.call_stall_kill_at = now + _CALL_STALL_STACK_GRACE_S
                    state.stalled = True
                    state.stall_when = "call"
                    state.hang_product_eligible = _hang_product_eligible_for_call_stall(
                        active_node
                    )
                    state.effective_call_limit_s = call_limit
                    if active_node != _UNKNOWN_NODE:
                        state.last_running_node_id = active_node
            elif call_stall_pending and now >= call_stall_kill_at:
                if not _kill_process_tree(proc):
                    state.kill_failed = True
                state.stop_watchdog.set()
                return
        if suite_timeout_s is not None and now - started >= suite_timeout_s:
            state.timed_out = True
            if not _kill_process_tree(proc):
                state.kill_failed = True
            state.stop_watchdog.set()
            return
        time.sleep(0.25)


def _enqueue_log_item(log_queue: queue.SimpleQueue[_LogWorkItem], item: _LogWorkItem) -> None:
    """Reader: неблокирующая постановка; при переполнении — отбрасываем запись, не pipe."""
    try:
        log_queue.put_nowait(item)
    except queue.Full:
        pass


def _log_consumer_loop(
    log_queue: queue.SimpleQueue[_LogWorkItem],
    state: _StreamWatchState,
) -> None:
    """Отдельный поток: ``logging`` здесь; зависший handler не блокирует чтение stdout/stderr."""
    while True:
        if state.stop_log_worker.is_set() and log_queue.empty():
            return
        try:
            item = log_queue.get(timeout=0.25)
        except queue.Empty:
            continue
        if item.kind == "progress":
            log.info(
                "verify quality_gates pytest_progress scope=%s phase=%s node_id=%s",
                item.scope,
                item.phase,
                item.node_id,
            )
            with state.lock:
                state.last_logged_phase = item.phase
                state.last_logged_node = item.node_id
        elif item.kind == "heartbeat":
            log.info(
                "verify quality_gates pytest_heartbeat scope=%s phase=%s node_id=%s silence_s=%s",
                item.scope,
                item.phase,
                item.node_id,
                item.silence_s,
            )
            state.last_heartbeat_log = time.monotonic()


def _maybe_enqueue_heartbeat(
    state: _StreamWatchState,
    *,
    scope: str,
    now: float,
    log_queue: queue.SimpleQueue[_LogWorkItem],
) -> None:
    """Снимок под lock, постановка heartbeat в очередь — без ``log.info`` в reader."""
    if now - state.last_heartbeat_log < _HEARTBEAT_INTERVAL_S:
        return
    with state.lock:
        silence_s = int(now - state.last_progress_at)
        phase = state.last_logged_phase or state.active_when or "unknown"
        node_id = state.last_node_id
        state.last_heartbeat_log = now
    _enqueue_log_item(
        log_queue,
        _LogWorkItem(
            kind="heartbeat",
            scope=scope,
            phase=str(phase),
            node_id=node_id,
            silence_s=silence_s,
        ),
    )


def _record_line(
    state: _StreamWatchState,
    line: str,
    *,
    scope: str,
    log_queue: queue.SimpleQueue[_LogWorkItem],
    default_call_limit: float,
) -> None:
    """Reader: только state + очередь логов; stdout/stderr вычитываются без ожидания handler."""
    stripped = line.strip()
    if any(marker in stripped for marker in _STACK_TRACE_MARKERS):
        with state.lock:
            state.stack_trace_seen = True
    segments = list(iter_opsx_gate_segments(line))
    node, phase, is_progress = _classify_pytest_line(line)
    now = time.monotonic()
    if segments:
        for gate in segments:
            log_phase: PytestPhase | None = None
            log_node: str | None = None
            should_log = False
            with state.lock:
                _apply_gate_event(state, gate, now, default_call_limit=default_call_limit)
                log_phase = gate["when"]
                log_node = gate["node"]
                if log_phase is not None and log_node:
                    if (
                        log_phase != state.last_logged_phase
                        or log_node != state.last_logged_node
                    ):
                        should_log = True
            if should_log and log_phase is not None and log_node:
                _enqueue_log_item(
                    log_queue,
                    _LogWorkItem(
                        kind="progress",
                        scope=scope,
                        phase=str(log_phase),
                        node_id=log_node,
                    ),
                )
    else:
        log_phase = None
        log_node = None
        should_log = False
        with state.lock:
            if is_progress:
                _apply_legacy_line(state, node, phase, now)
                log_phase = phase
                log_node = node or state.last_node_id
            if log_phase is not None and log_node:
                if log_phase != state.last_logged_phase or log_node != state.last_logged_node:
                    should_log = True
        if should_log and log_phase is not None and log_node:
            _enqueue_log_item(
                log_queue,
                _LogWorkItem(kind="progress", scope=scope, phase=str(log_phase), node_id=log_node),
            )
    _maybe_enqueue_heartbeat(state, scope=scope, now=now, log_queue=log_queue)


def run_pytest_gate_with_progress(
    command: list[str],
    *,
    cwd: Path,
    timeout_seconds: float | None,
    scope: str,
    test_count: int,
    per_test_seconds: float,
    stall_limit_seconds: float | None = None,
    call_phase_limit_seconds: float | None = None,
    extra_env: dict[str, str] | None = None,
) -> tuple[GateCheckResult, str]:
    """Запустить pytest с потоковым чтением stdout/stderr.

    Reader непрерывно дренирует pipe; `logging` — только в отдельном потоке через очередь.
    Сторож убивает дерево без вызова logging. Лимиты:
    - ``timeout_seconds`` — бюджет suite (``None`` — без верхнего лимита suite, только stall/call);
    - ``stall_limit_seconds`` — тишина без строки прогресса (300 с);
    - ``call_phase_limit_seconds`` — фаза ``call`` без ``report`` (per-test, см. QUALITY_GATE_PER_TEST_SECONDS).

    Убийство одного теста внутри pytest — ``pytest-timeout``; перед ``os._exit`` плагин
    делает flush и тоже может зависнуть — внешний сторож не ждёт flush.
    """
    stall_limit = (
        float(stall_limit_seconds)
        if stall_limit_seconds is not None
        else QUALITY_GATE_PYTEST_STALL_LIMIT_SECONDS
    )
    call_limit = (
        float(call_phase_limit_seconds)
        if call_phase_limit_seconds is not None
        else QUALITY_GATE_PER_TEST_SECONDS
    )
    cmd = argv_with_progress_verbosity(command)
    started = time.monotonic()
    env = _gate_pytest_env(cwd, extra_env)
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    state = _StreamWatchState(
        last_progress_at=started,
        last_heartbeat_log=started,
        call_phase_limit_effective=call_limit,
    )
    log_queue: queue.SimpleQueue[_LogWorkItem] = queue.SimpleQueue()

    def _reader(stream: object, bucket: list[str]) -> None:
        if stream is None:
            return
        while not state.stop_watchdog.is_set():
            raw = stream.readline()
            if not raw:
                break
            line = decode_subprocess_output(raw)
            bucket.append(line)
            _record_line(
                state,
                line,
                scope=scope,
                log_queue=log_queue,
                default_call_limit=call_limit,
            )

    log_worker = threading.Thread(
        target=_log_consumer_loop,
        args=(log_queue, state),
        daemon=True,
        name="pytest-gate-log",
    )
    log_worker.start()

    threads = [
        threading.Thread(target=_reader, args=(proc.stdout, stdout_chunks), daemon=True),
        threading.Thread(target=_reader, args=(proc.stderr, stderr_chunks), daemon=True),
    ]
    for thread in threads:
        thread.start()

    watchdog = threading.Thread(
        target=_watchdog_loop,
        args=(proc, state),
        kwargs={
            "started": started,
            "suite_timeout_s": timeout_seconds,
            "stall_limit_s": stall_limit,
            "call_limit_s": call_limit,
        },
        daemon=True,
    )
    watchdog.start()

    while proc.poll() is None and not state.stop_watchdog.is_set():
        time.sleep(0.25)

    state.stop_watchdog.set()
    state.stop_log_worker.set()
    watchdog.join(timeout=2)
    for thread in threads:
        thread.join(timeout=2)
    log_worker.join(timeout=2)

    stdout = "".join(stdout_chunks)
    stderr = "".join(stderr_chunks)
    duration_ms = int((time.monotonic() - started) * 1000)

    if state.kill_failed:
        payload = {
            "kind": "PYTEST_KILL_FAILED",
            "scope": scope,
            "pid": proc.pid,
            "attempts": _TASKKILL_MAX_ATTEMPTS,
        }
        full_log = _combine_stdio(stdout, stderr)
        return (
            GateCheckResult(
                check_id="pytest",  # type: ignore[arg-type]
                command=cmd,
                exit_code=1,
                duration_ms=duration_ms,
                stdout_tail=_tail(stdout),
                stderr_tail=_tail(stderr),
                error=json.dumps(payload, ensure_ascii=False),
            ),
            full_log,
        )

    stalled = state.stalled
    timed_out = state.timed_out
    if stalled or timed_out:
        from workflows.verify.gate_repair.diagnostics import resolve_stall_last_node_id

        node_id = (
            state.last_running_node_id
            if state.last_running_node_id != _UNKNOWN_NODE
            else state.last_node_id
        )
        node_id = resolve_stall_last_node_id(node_id, stdout)
        reason = "PYTEST_STALL" if stalled else "PYTEST_SUITE_TIMEOUT"
        stdout = stdout + f"\nFAILED {node_id} - {reason}\n"
        if stalled:
            call_limit_recorded = (
                state.effective_call_limit_s
                if state.effective_call_limit_s > 0
                else (
                    state.call_phase_limit_effective
                    if state.call_phase_limit_effective > 0
                    else call_limit
                )
            )
            stack_status = "captured" if state.stack_trace_seen else "absent"
            function_analysis: dict[str, object] | None = None
            if (
                state.hang_product_eligible
                and stack_status == "captured"
                and state.stall_when == "call"
            ):
                row = stall_function_analysis_from_stderr(stderr, cwd)
                if row is not None:
                    function_analysis = dict(row)
            evidence = format_pytest_stall_evidence(
                scope=scope,
                stall_limit_s=stall_limit,
                timeout_s=timeout_seconds,
                test_count=test_count,
                per_test_s=per_test_seconds,
                last_node_id=node_id,
                stdout_tail=stdout,
                stderr_tail=stderr,
                stall_when=state.stall_when,
                hang_product_eligible=state.hang_product_eligible,
                call_limit_s=call_limit_recorded,
                stack_status=stack_status,
                stall_function_analysis=function_analysis,
            )
        else:
            evidence = format_pytest_timeout_evidence(
                scope=scope,
                timeout_s=timeout_seconds,
                test_count=test_count,
                per_test_s=per_test_seconds,
                last_node_id=node_id,
                stdout_tail=stdout,
                stderr_tail=stderr,
            )
        full_log = _combine_stdio(stdout, stderr)
        return (
            GateCheckResult(
                check_id="pytest",  # type: ignore[arg-type]
                command=cmd,
                exit_code=1,
                duration_ms=duration_ms,
                stdout_tail=_tail(stdout),
                stderr_tail=_tail(stderr),
                error=evidence,
            ),
            full_log,
        )

    exit_code = int(proc.returncode or 0)
    full_log = _combine_stdio(stdout, stderr)
    return (
        GateCheckResult(
            check_id="pytest",  # type: ignore[arg-type]
            command=cmd,
            exit_code=exit_code,
            duration_ms=duration_ms,
            stdout_tail=_tail(stdout),
            stderr_tail=_tail(stderr),
            error=None,
        ),
        full_log,
    )
