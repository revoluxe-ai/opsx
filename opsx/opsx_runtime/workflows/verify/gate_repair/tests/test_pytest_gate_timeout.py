"""Тесты динамического timeout pytest gates и evidence при зависании."""

from __future__ import annotations

import io
import json
import logging
import threading
import time
from pathlib import Path

import pytest

from workflows.verify.gate_repair.diagnostics import parse_pytest_issues
from workflows.verify.gate_repair.pytest_gate_timeout import (
    QUALITY_GATE_MIN_TIMEOUT_SECONDS,
    QUALITY_GATE_PER_TEST_SECONDS,
    QUALITY_GATE_PYTEST_STALL_LIMIT_SECONDS,
    parse_collected_test_count,
    quality_gate_subprocess_safety_seconds,
    resolve_pytest_gate_timeout_seconds,
)
from workflows.verify.gate_repair.pytest_subprocess_streaming import (
    _LONG_RUNNING_SMOKE_CALL_LIMIT_S,
    _SUBPROCESS_SPAWN_CALL_LIMIT_S,
    _effective_call_limit_for_node,
    _hang_product_eligible_for_call_stall,
    is_pytest_progress_line,
    run_pytest_gate_with_progress,
)


def _mark_killed(proc: object) -> bool:
    """Имитация успешного ``taskkill``: дерево мертво, сторож не уходит в ``PYTEST_KILL_FAILED``."""
    if hasattr(proc, "_returncode"):
        setattr(proc, "_returncode", -9)
    kill = getattr(proc, "kill", None)
    if callable(kill):
        kill()
    return True


def test_resolve_pytest_gate_timeout_formula() -> None:
    safety = quality_gate_subprocess_safety_seconds()
    assert safety > 90.0
    expected = max(QUALITY_GATE_MIN_TIMEOUT_SECONDS, safety)
    assert resolve_pytest_gate_timeout_seconds(0) == expected
    assert resolve_pytest_gate_timeout_seconds(4) == expected
    assert resolve_pytest_gate_timeout_seconds(2023) == expected
    assert resolve_pytest_gate_timeout_seconds(100, caller_cap_seconds=90) == 90
    assert resolve_pytest_gate_timeout_seconds(10, caller_cap_seconds=999) == expected


def test_parse_collected_test_count_from_tail() -> None:
    tail = "collected 42 items / 2 deselected / 40 selected\n\n======== 40 tests collected in 1.23s ========"
    assert parse_collected_test_count(tail) == 40


class _FakeStream(io.BytesIO):
    def __init__(self, lines: list[bytes]) -> None:
        super().__init__(b"".join(lines))
        self._lines = lines
        self._pos = 0

    def readline(self) -> bytes:
        if self._pos >= len(self._lines):
            return b""
        line = self._lines[self._pos]
        self._pos += 1
        return line


class _HangingFakeProcess:
    @property
    def returncode(self) -> int | None:
        return self._returncode

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.pid = 4242
        self.stdout = _FakeStream(
            [
                b"tests/unit/foo.py::test_one PASSED\n",
                b"tests/unit/foo.py::test_two \n",
            ]
        )
        self.stderr = _FakeStream([])
        self._returncode: int | None = None

    def poll(self) -> int | None:
        return self._returncode

    def wait(self, timeout: float | None = None) -> int:
        _ = timeout
        return 0

    def kill(self) -> None:
        self._returncode = -9


def test_pytest_timeout_returns_failed_node(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming.subprocess.Popen",
        _HangingFakeProcess,
    )
    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming._kill_process_tree",
        _mark_killed,
    )
    ticks = {"t": 0.0}

    def fake_monotonic() -> float:
        ticks["t"] += 2.0
        return ticks["t"]

    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming.time.monotonic",
        fake_monotonic,
    )
    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming.time.sleep",
        lambda _s: None,
    )

    gate, full_log = run_pytest_gate_with_progress(
        ["python", "-m", "pytest", "tests"],
        cwd=tmp_path,
        timeout_seconds=1.0,
        scope="FULL_SUITE",
        test_count=2,
        per_test_seconds=QUALITY_GATE_PER_TEST_SECONDS,
    )
    assert gate.exit_code == 1
    assert "FAILED tests/unit/foo.py::test_two - PYTEST_SUITE_TIMEOUT" in full_log
    evidence = json.loads(gate.error or "{}")
    assert evidence["last_node_id"] == "tests/unit/foo.py::test_two"
    assert evidence["test_count"] == 2
    assert any("PYTEST_SUITE_TIMEOUT" in item for item in parse_pytest_issues(full_log))


class _SilentAfterFirstLineProcess:
    """Одна строка хода, дальше тишина — имитация зависания pytest."""

    @property
    def returncode(self) -> int | None:
        return self._returncode

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.pid = 5151
        self.stdout = _FakeStream([b"tests/unit/bar.py::test_stuck PASSED\n"])
        self.stderr = _FakeStream([])
        self._returncode: int | None = None

    def poll(self) -> int | None:
        return self._returncode

    def wait(self, timeout: float | None = None) -> int:
        _ = timeout
        return 0

    def kill(self) -> None:
        self._returncode = -9


def test_pytest_stall_returns_failed_node(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """300 с без новой строки stdout/stderr — failed gate ``PYTEST_STALL``, не обрыв графа."""
    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming.QUALITY_GATE_PYTEST_STALL_LIMIT_SECONDS",
        4.0,
    )
    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming.subprocess.Popen",
        _SilentAfterFirstLineProcess,
    )
    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming._kill_process_tree",
        _mark_killed,
    )
    anchor = {"t": time.monotonic()}

    def fake_monotonic() -> float:
        anchor["t"] += 5.0
        return anchor["t"]

    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming.time.monotonic",
        fake_monotonic,
    )
    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming.time.sleep",
        lambda _s: None,
    )

    gate, full_log = run_pytest_gate_with_progress(
        ["python", "-m", "pytest", "tests"],
        cwd=tmp_path,
        timeout_seconds=QUALITY_GATE_PYTEST_STALL_LIMIT_SECONDS * 100,
        scope="FULL_SUITE",
        test_count=1,
        per_test_seconds=QUALITY_GATE_PER_TEST_SECONDS,
        stall_limit_seconds=4.0,
    )
    assert gate.exit_code == 1
    assert "FAILED tests/unit/bar.py::test_stuck - PYTEST_STALL" in full_log
    evidence = json.loads(gate.error or "{}")
    assert evidence["stall_limit_s"] == 4.0
    assert evidence["last_node_id"] == "tests/unit/bar.py::test_stuck"


def test_pytest_stall_without_suite_cap_when_timeout_none(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``timeout_seconds=None``: сторож не сравнивает suite budget (нет TypeError, stall срабатывает)."""
    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming.QUALITY_GATE_PYTEST_STALL_LIMIT_SECONDS",
        4.0,
    )
    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming.subprocess.Popen",
        _SilentAfterFirstLineProcess,
    )
    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming._kill_process_tree",
        _mark_killed,
    )
    anchor = {"t": time.monotonic()}

    def fake_monotonic() -> float:
        anchor["t"] += 5.0
        return anchor["t"]

    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming.time.monotonic",
        fake_monotonic,
    )
    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming.time.sleep",
        lambda _s: None,
    )

    gate, full_log = run_pytest_gate_with_progress(
        ["python", "-m", "pytest", "tests"],
        cwd=tmp_path,
        timeout_seconds=None,
        scope="single-probe",
        test_count=1,
        per_test_seconds=QUALITY_GATE_PER_TEST_SECONDS,
        stall_limit_seconds=4.0,
    )
    assert gate.exit_code == 1
    assert "PYTEST_STALL" in full_log
    assert "PYTEST_SUITE_TIMEOUT" not in full_log


class _InfiniteNoiseStream:
    """Имитация subprocess, который печатает мусор без pytest-прогресса."""

    def readline(self) -> bytes:
        return b"INFO: application log spam without node id\n"


class _NoiseOnlyHangProcess:
    @property
    def returncode(self) -> int | None:
        return self._returncode

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.pid = 6161
        self.stdout = _InfiniteNoiseStream()
        self.stderr = _InfiniteNoiseStream()
        self._returncode: int | None = None

    def poll(self) -> int | None:
        return self._returncode

    def kill(self) -> None:
        self._returncode = -9


def test_pytest_stall_kills_on_non_progress_output_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Посторонний вывод без строки хода — ``PYTEST_STALL``, не бесконечное ожидание."""
    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming.subprocess.Popen",
        _NoiseOnlyHangProcess,
    )
    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming._kill_process_tree",
        _mark_killed,
    )
    anchor = {"t": 0.0}

    def fake_monotonic() -> float:
        anchor["t"] += 5.0
        return anchor["t"]

    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming.time.monotonic",
        fake_monotonic,
    )
    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming.time.sleep",
        lambda _s: None,
    )
    gate, full_log = run_pytest_gate_with_progress(
        ["python", "-m", "pytest", "tests"],
        cwd=tmp_path,
        timeout_seconds=10_000.0,
        scope="FULL_SUITE",
        test_count=1,
        per_test_seconds=QUALITY_GATE_PER_TEST_SECONDS,
        stall_limit_seconds=4.0,
    )
    assert gate.exit_code == 1
    assert "PYTEST_STALL" in full_log
    assert not is_pytest_progress_line("INFO: application log spam without node id\n")


class _QuickPassProcess:
    @property
    def returncode(self) -> int | None:
        return self._returncode

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.pid = 7171
        self.stdout = _FakeStream(
            [
                b"tests/unit/baz.py::test_ok PASSED\n",
                b"======================== 1 passed in 0.01s ========================\n",
            ]
        )
        self.stderr = _FakeStream([])
        self._returncode: int | None = 0

    def poll(self) -> int | None:
        return self._returncode


def test_pytest_progress_lines_classified() -> None:
    assert is_pytest_progress_line("tests/a.py::test_x PASSED\n")
    assert is_pytest_progress_line("collecting ...\n")
    assert is_pytest_progress_line("======================== 3 tests collected in 0.01s ========================\n")
    assert not is_pytest_progress_line("DEBUG: random logger output\n")


def test_pytest_stall_not_triggered_while_progress_lines_arrive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Короткий прогон с реальными строками прогресса завершается без ``PYTEST_STALL``."""
    killed = {"n": 0}
    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming.subprocess.Popen",
        _QuickPassProcess,
    )
    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming._kill_process_tree",
        lambda proc: killed.__setitem__("n", killed["n"] + 1),
    )
    ticks = {"t": 0.0}

    def fake_monotonic() -> float:
        ticks["t"] += 1.0
        return ticks["t"]

    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming.time.monotonic",
        fake_monotonic,
    )
    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming.time.sleep",
        lambda _s: None,
    )
    gate, full_log = run_pytest_gate_with_progress(
        ["python", "-m", "pytest", "tests"],
        cwd=tmp_path,
        timeout_seconds=10_000.0,
        scope="FULL_SUITE",
        test_count=1,
        per_test_seconds=QUALITY_GATE_PER_TEST_SECONDS,
        stall_limit_seconds=4.0,
    )
    assert killed["n"] == 0
    assert gate.error is None
    assert "PYTEST_STALL" not in full_log


class _HangAfterOpsxCallStart:
    """После строки call=start readline блокируется — имитация зависшего call."""

    def __init__(self) -> None:
        self._sent = False

    def readline(self) -> bytes:
        if not self._sent:
            self._sent = True
            return (
                b"OPSX_GATE_PROGRESS node=tests/unit/call.py::test_slow when=call event=start\n"
            )
        time.sleep(10_000)
        return b""


class _CallHangProcess:
    @property
    def returncode(self) -> int | None:
        return self._returncode

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.pid = 8181
        self.stdout = _HangAfterOpsxCallStart()
        self.stderr = _FakeStream([])
        self._returncode: int | None = None

    def poll(self) -> int | None:
        return self._returncode

    def kill(self) -> None:
        self._returncode = -9


def test_pytest_call_phase_stall_without_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Открытая фаза call без report — ``PYTEST_STALL``, ``hang_product_eligible`` true."""
    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming.subprocess.Popen",
        _CallHangProcess,
    )
    killed: list[int] = []

    def _kill(proc: object) -> bool:
        killed.append(1)
        proc_ref = proc  # type: ignore[assignment]
        assert isinstance(proc_ref, _CallHangProcess)
        proc_ref._returncode = -9
        return True

    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming._kill_process_tree",
        _kill,
    )
    anchor = {"t": 0.0}

    def fake_monotonic() -> float:
        anchor["t"] += 20.0
        return anchor["t"]

    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming.time.monotonic",
        fake_monotonic,
    )
    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming.time.sleep",
        lambda _s: None,
    )
    gate, full_log = run_pytest_gate_with_progress(
        ["python", "-m", "pytest", "tests"],
        cwd=tmp_path,
        timeout_seconds=10_000.0,
        scope="FULL_SUITE",
        test_count=1,
        per_test_seconds=QUALITY_GATE_PER_TEST_SECONDS,
        stall_limit_seconds=500.0,
        call_phase_limit_seconds=4.0,
    )
    assert killed
    assert gate.exit_code == 1
    assert "PYTEST_STALL" in full_log
    evidence = json.loads(gate.error or "{}")
    assert evidence.get("stall_when") == "call"
    assert evidence.get("hang_product_eligible") is True


class _BlockEmitHandler(logging.Handler):
    """Блокирует emit, пока ``release`` не установлен — имитация зависшего logging handler."""

    def __init__(self, release: threading.Event) -> None:
        super().__init__()
        self._release = release

    def emit(self, record: logging.LogRecord) -> None:
        _ = record
        self._release.wait(timeout=120.0)


class _BlockOnSecondLineStream:
    def __init__(self) -> None:
        self._lines = [
            b"OPSX_GATE_PROGRESS node=tests/unit/log.py::test_x when=call event=start\n",
            b"EXTRA verbose line to trigger log.info\n",
        ]
        self._pos = 0

    def readline(self) -> bytes:
        if self._pos >= len(self._lines):
            time.sleep(10_000)
            return b""
        line = self._lines[self._pos]
        self._pos += 1
        return line


class _LogBlockProcess:
    @property
    def returncode(self) -> int | None:
        return self._returncode

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.pid = 9191
        self.stdout = _BlockOnSecondLineStream()
        self.stderr = _FakeStream([])
        self._returncode: int | None = None

    def poll(self) -> int | None:
        return self._returncode

    def kill(self) -> None:
        self._returncode = -9


def test_watchdog_kills_when_logging_handler_blocks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Заблокированный ``logging.Handler`` на log-worker — сторож убивает subprocess; reader не ждёт emit."""
    release = threading.Event()
    routing_logger = logging.getLogger("opsx.verify.routing")
    handler = _BlockEmitHandler(release)
    routing_logger.addHandler(handler)
    routing_logger.setLevel(logging.INFO)
    try:
        monkeypatch.setattr(
            "workflows.verify.gate_repair.pytest_subprocess_streaming.subprocess.Popen",
            _LogBlockProcess,
        )
        killed: list[int] = []

        def _kill(proc: object) -> bool:
            killed.append(1)
            proc_ref = proc  # type: ignore[assignment]
            assert isinstance(proc_ref, _LogBlockProcess)
            proc_ref._returncode = -9
            return True

        monkeypatch.setattr(
            "workflows.verify.gate_repair.pytest_subprocess_streaming._kill_process_tree",
            _kill,
        )
        anchor = {"t": 0.0}

        def fake_monotonic() -> float:
            anchor["t"] += 20.0
            return anchor["t"]

        monkeypatch.setattr(
            "workflows.verify.gate_repair.pytest_subprocess_streaming.time.monotonic",
            fake_monotonic,
        )
        monkeypatch.setattr(
            "workflows.verify.gate_repair.pytest_subprocess_streaming.time.sleep",
            lambda _s: None,
        )
        gate, _full_log = run_pytest_gate_with_progress(
            ["python", "-m", "pytest", "tests"],
            cwd=tmp_path,
            timeout_seconds=10_000.0,
            scope="FULL_SUITE",
            test_count=1,
            per_test_seconds=QUALITY_GATE_PER_TEST_SECONDS,
            call_phase_limit_seconds=4.0,
            stall_limit_seconds=500.0,
        )
        assert killed
        assert gate.exit_code == 1
        assert gate.error is not None
    finally:
        release.set()
        routing_logger.removeHandler(handler)
        handler.close()


def test_pytest_completes_while_logging_handler_blocks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Контроль: зависший handler на routing — дочерний «pytest» всё равно доезжает до exit 0."""
    release = threading.Event()
    routing_logger = logging.getLogger("opsx.verify.routing")
    handler = _BlockEmitHandler(release)
    killed = {"n": 0}
    routing_logger.addHandler(handler)
    routing_logger.setLevel(logging.INFO)
    try:
        monkeypatch.setattr(
            "workflows.verify.gate_repair.pytest_subprocess_streaming.subprocess.Popen",
            _QuickPassProcess,
        )
        monkeypatch.setattr(
            "workflows.verify.gate_repair.pytest_subprocess_streaming._kill_process_tree",
            lambda proc: killed.__setitem__("n", killed["n"] + 1) or False,
        )
        gate, full_log = run_pytest_gate_with_progress(
            ["python", "-m", "pytest", "tests"],
            cwd=tmp_path,
            timeout_seconds=10_000.0,
            scope="FULL_SUITE",
            test_count=1,
            per_test_seconds=QUALITY_GATE_PER_TEST_SECONDS,
            stall_limit_seconds=4.0,
        )
        assert killed["n"] == 0
        assert gate.exit_code == 0
        assert gate.error is None
        assert "PASSED" in full_log
    finally:
        release.set()
        routing_logger.removeHandler(handler)
        handler.close()


class _SkippedThenHangStream:
    def __init__(self) -> None:
        self._lines = [
            b"OPSX_GATE_PROGRESS node=tests/unit/skip.py::test_z when=call event=start\n",
            b"OPSX_GATE_PROGRESS node=tests/unit/skip.py::test_z when=call event=report outcome=skipped\n",
        ]
        self._pos = 0

    def readline(self) -> bytes:
        if self._pos >= len(self._lines):
            time.sleep(10_000)
            return b""
        line = self._lines[self._pos]
        self._pos += 1
        return line


class _SkippedCallProcess:
    @property
    def returncode(self) -> int | None:
        return self._returncode

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        self.pid = 1010
        self.stdout = _SkippedThenHangStream()
        self.stderr = _FakeStream([])
        self._returncode: int | None = None

    def poll(self) -> int | None:
        return self._returncode

    def kill(self) -> None:
        self._returncode = -9


def test_skipped_call_does_not_trigger_call_phase_kill(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``outcome=skipped`` закрывает call — убийство только по suite timeout, не call 15 с."""
    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming.subprocess.Popen",
        _SkippedCallProcess,
    )
    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming._kill_process_tree",
        _mark_killed,
    )
    anchor = {"t": 0.0}

    def fake_monotonic() -> float:
        anchor["t"] += 5.0
        return anchor["t"]

    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming.time.monotonic",
        fake_monotonic,
    )
    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming.time.sleep",
        lambda _s: None,
    )
    gate, full_log = run_pytest_gate_with_progress(
        ["python", "-m", "pytest", "tests"],
        cwd=tmp_path,
        timeout_seconds=3.0,
        scope="FULL_SUITE",
        test_count=1,
        per_test_seconds=QUALITY_GATE_PER_TEST_SECONDS,
        call_phase_limit_seconds=4.0,
        stall_limit_seconds=500.0,
    )
    assert gate.exit_code == 1
    assert "PYTEST_SUITE_TIMEOUT" in full_log
    assert "PYTEST_STALL" not in full_log


def test_kill_failed_returns_explicit_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming.subprocess.Popen",
        _SilentAfterFirstLineProcess,
    )
    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming._kill_process_tree",
        lambda _proc: False,
    )
    anchor = {"t": 0.0}

    def fake_monotonic() -> float:
        anchor["t"] += 5.0
        return anchor["t"]

    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming.time.monotonic",
        fake_monotonic,
    )
    monkeypatch.setattr(
        "workflows.verify.gate_repair.pytest_subprocess_streaming.time.sleep",
        lambda _s: None,
    )
    gate, full_log = run_pytest_gate_with_progress(
        ["python", "-m", "pytest", "tests"],
        cwd=tmp_path,
        timeout_seconds=10_000.0,
        scope="FULL_SUITE",
        test_count=1,
        per_test_seconds=QUALITY_GATE_PER_TEST_SECONDS,
        stall_limit_seconds=4.0,
    )
    assert gate.exit_code == 1
    payload = json.loads(gate.error or "{}")
    assert payload.get("kind") == "PYTEST_KILL_FAILED"
    assert "PYTEST_STALL" not in full_log


def test_long_running_smoke_nodes_get_extended_call_budget() -> None:
    """Bootstrap smoke (openspec/poetry) не режется product call-limit 5 с."""
    node = (
        "tests/smoke/test_openspec_cli_provider_smoke.py::"
        "test_repo_wrapper_reports_expected_version"
    )
    assert _effective_call_limit_for_node(node, QUALITY_GATE_PER_TEST_SECONDS) == (
        _LONG_RUNNING_SMOKE_CALL_LIMIT_S
    )
    assert _hang_product_eligible_for_call_stall(node) is False


def test_subprocess_spawn_unit_gets_extended_call_budget() -> None:
    """``test_validate_injected_db_urls`` под suite load может молчать в call."""
    node = (
        "tests/unit/test_validate_injected_db_urls.py::"
        "test_error_stderr_never_contains_connection_secrets"
    )
    assert _effective_call_limit_for_node(node, QUALITY_GATE_PER_TEST_SECONDS) == (
        _SUBPROCESS_SPAWN_CALL_LIMIT_S
    )
    assert _hang_product_eligible_for_call_stall(node) is False


def test_opsx_continue_skill_contract_gets_extended_call_budget() -> None:
    """Preflight subprocess в skill-contract под полным suite может превышать product call-limit."""
    node = (
        "tests/unit/tools/test_opsx_continue_skill_contract.py::"
        "test_positive_fixture_passes_alignment_guard_and_preflight"
    )
    assert _effective_call_limit_for_node(node, QUALITY_GATE_PER_TEST_SECONDS) == (
        _SUBPROCESS_SPAWN_CALL_LIMIT_S
    )
    assert _hang_product_eligible_for_call_stall(node) is False
