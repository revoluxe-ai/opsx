"""Focused fail-closed and pass-path tests for manual /opsx-run-tests helper."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


HELPER = Path(__file__).resolve().parents[1] / "run-tests" / "run_tests.py"


def _load_module() -> Any:
    """Import the run_tests helper from its pipeline path."""

    spec = importlib.util.spec_from_file_location("run_tests_helper", HELPER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load helper from {HELPER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_tests_helper"] = module
    spec.loader.exec_module(module)
    return module


def _synthetic_repo(tmp_path: Path, relative: str, source: str) -> Path:
    """Create a minimal repo root with one tests/ module."""

    root = tmp_path / "repo"
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    _ = target.write_text(source, encoding="utf-8")
    return root


def _safe_module_source(destructive: bool = False) -> str:
    """Return a tiny pytest module for positive-path checks."""

    decorator = "@pytest.mark.destructive_local\n" if destructive else ""
    return (
        "import pytest\n\n"
        f"{decorator}"
        "def test_ok():\n"
        "    assert True\n\n"
        "def test_missing():\n"
        "    assert False\n"
    )


def test_path_traversal_blocked(tmp_path: Path) -> None:
    """Reject parent-segment paths before touching the filesystem."""

    module = _load_module()
    root = _synthetic_repo(tmp_path, "tests/unit/test_safe.py", _safe_module_source())
    result = module.evaluate_manual_run(
        root,
        "mode=manual lane=pytest test_ids=tests/../secrets.py::test_ok",
    )
    assert result.status == "BLOCKED"
    assert result.reason == "PATH_TRAVERSAL"
    assert result.runner_invoked == 0


def test_missing_test_id_blocked(tmp_path: Path) -> None:
    """Reject empty selections and ids without pytest node suffix."""

    module = _load_module()
    root = _synthetic_repo(tmp_path, "tests/unit/test_safe.py", _safe_module_source())
    empty = module.evaluate_manual_run(
        root, "mode=manual lane=pytest test_ids="
    )
    assert empty.status == "BLOCKED"
    assert empty.reason == "MISSING_TEST_ID"

    bare_file = module.evaluate_manual_run(
        root, "mode=manual lane=pytest test_ids=tests/unit/test_safe.py"
    )
    assert bare_file.status == "BLOCKED"
    assert bare_file.reason == "MISSING_TEST_ID"


def test_duplicate_test_id_blocked(tmp_path: Path) -> None:
    """Reject duplicate explicit ids in one manual invocation."""

    module = _load_module()
    root = _synthetic_repo(tmp_path, "tests/unit/test_safe.py", _safe_module_source())
    node = "tests/unit/test_safe.py::test_ok"
    result = module.evaluate_manual_run(
        root,
        f"mode=manual lane=pytest test_ids={node},{node}",
    )
    assert result.status == "BLOCKED"
    assert result.reason == "DUPLICATE_TEST_ID"


def test_shell_injection_characters_blocked(tmp_path: Path) -> None:
    """Reject shell metacharacters embedded in node ids."""

    module = _load_module()
    root = _synthetic_repo(tmp_path, "tests/unit/test_safe.py", _safe_module_source())
    result = module.evaluate_manual_run(
        root,
        "mode=manual lane=pytest test_ids=tests/unit/test_safe.py::test_ok|whoami",
    )
    assert result.status == "BLOCKED"
    assert result.reason == "INVALID_TEST_ID"
    assert result.runner_invoked == 0


def test_wrong_lane_blocked(tmp_path: Path) -> None:
    """Reject non-pytest lane tokens and paths outside tests/."""

    module = _load_module()
    root = _synthetic_repo(tmp_path, "tests/unit/test_safe.py", _safe_module_source())
    lane = module.evaluate_manual_run(
        root,
        "mode=manual lane=frontend test_ids=tests/unit/test_safe.py::test_ok",
    )
    assert lane.status == "BLOCKED"
    assert lane.reason == "CROSS_LANE"
    assert lane.detail == "LANE_NOT_PYTEST"

    path_lane = module.evaluate_manual_run(
        root,
        "mode=manual lane=pytest test_ids=app/unit/test_safe.py::test_ok",
    )
    assert path_lane.status == "BLOCKED"
    assert path_lane.reason == "CROSS_LANE"


def test_destructive_marker_blocked(tmp_path: Path) -> None:
    """Fail closed when destructive_local is present on the selected node."""

    module = _load_module()
    root = _synthetic_repo(
        tmp_path, "tests/unit/test_destructive.py", _safe_module_source(destructive=True)
    )
    result = module.evaluate_manual_run(
        root,
        "mode=manual lane=pytest test_ids=tests/unit/test_destructive.py::test_ok",
    )
    assert result.status == "BLOCKED"
    assert result.reason == "DESTRUCTIVE_LOCAL_BLOCKED"
    assert "destructive_local" in result.detail
    assert result.runner_invoked == 0


def test_missing_pytest_node_blocked(tmp_path: Path) -> None:
    """Reject selectors that do not resolve to an existing test function."""

    module = _load_module()
    root = _synthetic_repo(tmp_path, "tests/unit/test_safe.py", _safe_module_source())
    missing = module.evaluate_manual_run(
        root,
        "mode=manual lane=pytest test_ids=tests/unit/test_safe.py::test_not_defined",
    )
    assert missing.status == "BLOCKED"
    assert missing.reason == "UNKNOWN_SELECTOR"

    missing_file = module.evaluate_manual_run(
        root,
        "mode=manual lane=pytest test_ids=tests/unit/test_absent.py::test_ok",
    )
    assert missing_file.status == "BLOCKED"
    assert missing_file.reason == "MISSING_TEST_ID"


def test_safe_explicit_dummy_node_passes(tmp_path: Path) -> None:
    """Run a validated node with an injected runner and expect PASS."""

    module = _load_module()
    root = _synthetic_repo(tmp_path, "tests/unit/test_safe.py", _safe_module_source())
    captured: list[list[str]] = []

    def fake_runner(argv: tuple[str, ...] | list[str], cwd: Path) -> int:
        captured.append(list(argv))
        assert cwd == root.resolve()
        assert argv[0] == "poetry"
        assert argv[2] == "pytest"
        return 0

    result = module.evaluate_manual_run(
        root,
        "mode=manual lane=pytest test_ids=tests/unit/test_safe.py::test_ok",
        runner=fake_runner,
    )
    assert result.status == "PASS"
    assert result.reason == ""
    assert result.test_ids == ("tests/unit/test_safe.py::test_ok",)
    assert result.exit_codes == (0,)
    assert len(captured) == 1
    assert captured[0][3] == "tests/unit/test_safe.py::test_ok"


def test_bounded_refuses_without_executor(tmp_path: Path) -> None:
    """Bounded mode stays blocked on this target (no S6 pytest executor wiring)."""

    module = _load_module()
    root = _synthetic_repo(tmp_path, "tests/unit/test_safe.py", _safe_module_source())
    result = module.evaluate_manual_run(
        root,
        "mode=bounded lane=pytest test_ids=tests/unit/test_safe.py::test_ok",
    )
    assert result.status == "BLOCKED"
    assert result.reason == "BINDING_GAP"
    assert result.detail == "S6_TEST_CAPABILITY_UNREGISTERED"
    assert result.runner_invoked == 0


def test_runner_failure_blocked(tmp_path: Path) -> None:
    """Surface unexpected runner exits as BLOCKED execution errors."""

    module = _load_module()
    root = _synthetic_repo(tmp_path, "tests/unit/test_safe.py", _safe_module_source())

    def crash_runner(argv: tuple[str, ...] | list[str], cwd: Path) -> int:
        _ = argv
        _ = cwd
        return 2

    result = module.evaluate_manual_run(
        root,
        "mode=manual lane=pytest test_ids=tests/unit/test_safe.py::test_ok",
        runner=crash_runner,
    )
    assert result.status == "BLOCKED"
    assert result.reason == "EXECUTION_ERROR"
    assert result.exit_codes == (2,)

    def collection_runner(argv: tuple[str, ...] | list[str], cwd: Path) -> int:
        _ = argv
        _ = cwd
        return 4

    invalid = module.evaluate_manual_run(
        root,
        "mode=manual lane=pytest test_ids=tests/unit/test_safe.py::test_ok",
        runner=collection_runner,
    )
    assert invalid.status == "BLOCKED"
    assert invalid.reason == "INVALID_TEST_ID"
    assert invalid.exit_codes == (4,)


def test_test_failure_status(tmp_path: Path) -> None:
    """Map pytest exit code 1 to FAIL without claiming verification PASS."""

    module = _load_module()
    root = _synthetic_repo(tmp_path, "tests/unit/test_safe.py", _safe_module_source())

    def failing_runner(argv: tuple[str, ...] | list[str], cwd: Path) -> int:
        _ = argv
        _ = cwd
        return 1

    result = module.evaluate_manual_run(
        root,
        "mode=manual lane=pytest test_ids=tests/unit/test_safe.py::test_ok",
        runner=failing_runner,
    )
    assert result.status == "FAIL"
    assert result.reason == "TEST_FAILED"
