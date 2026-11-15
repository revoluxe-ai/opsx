"""Маршрут timeout/stall: агент длительности, не test triage и не обрыв verify."""

from __future__ import annotations

from pathlib import Path

from agents.verify.dispatcher.agent import dispatcher_route_edge
from agents.verify.test_duration.stall_function_analysis import (
    stall_function_analysis_from_stderr,
)
from agents.verify.test_duration.timeout_policy import (
    clamp_personal_timeout_seconds,
    deterministic_duration_classification,
    hang_product_proven_from_stall_evidence,
    personal_timeout_extra_seconds,
    write_pytest_timeout_marker,
)
from workflows.verify.gate_repair.routing import route_after_quality_gates_with_classifier
from workflows.verify.dispatch_cycle.routing import duration_route_edge


def test_stall_diagnostics_go_to_classifier() -> None:
    state = {
        "quality_gate_verdict": "FAIL",
        "quality_gate_diagnostics": {
            "pytest": ["pytest: tests/unit/foo.py::test_slow - PYTEST_STALL"],
        },
    }
    assert route_after_quality_gates_with_classifier(state) == "pytest_error_classifier"


def test_timeout_finding_goes_to_duration_agent_not_test_triage() -> None:
    stall = {
        "dispatcher_route": "test",
        "dispatcher_current_item": {
            "observation": "pytest: tests/unit/foo.py::test_slow - PYTEST_STALL",
        },
    }
    assert dispatcher_route_edge(stall) == "test_duration"
    ordinary = {
        "dispatcher_route": "test",
        "dispatcher_current_item": {
            "observation": "pytest: tests/unit/foo.py::test_slow - AssertionError: boom",
        },
    }
    assert dispatcher_route_edge(ordinary) == "test_triage"


def test_duration_classifications_route() -> None:
    assert duration_route_edge(
        {"test_duration_result": {"classification": "HANG_PRODUCT"}},
    ) == "logic_bug_repair"
    assert duration_route_edge(
        {"test_duration_result": {"classification": "HANG_TEST"}},
    ) == "test_repair"
    assert duration_route_edge(
        {"test_duration_result": {"classification": "PERSONAL_TIMEOUT"}},
    ) == "pytest_personal_timeout"
    assert duration_route_edge(
        {
            "test_duration_result": {"classification": "PERSONAL_TIMEOUT"},
            "duration_probe_exhausted": True,
        },
    ) == "test_finding_finalize"
    assert duration_route_edge(
        {"test_duration_result": {"classification": "UNCLEAR"}},
    ) == "test_finding_finalize"


def test_deterministic_suite_timeout_on_harness_collect_is_hang_test() -> None:
    node = "tests/unit/test_pytest_collection_scope.py::test_broad_pytest_dot_skips_non_product_trees"
    obs = f"pytest: {node} - PYTEST_SUITE_TIMEOUT"
    assert deterministic_duration_classification(observation=obs, node_id=node) == "HANG_TEST"


def test_product_suite_timeout_needs_duration_agent() -> None:
    node = "tests/unit/test_telegram_proxy.py::test_no_proxy_env_has_no_mounts"
    obs = f"pytest: {node} - PYTEST_SUITE_TIMEOUT"
    assert deterministic_duration_classification(observation=obs, node_id=node) is None


def test_product_stall_without_proof_needs_duration_agent() -> None:
    node = "tests/unit/foo.py::test_slow"
    obs = f"pytest: {node} - PYTEST_STALL"
    assert deterministic_duration_classification(observation=obs, node_id=node) is None


def test_product_stall_with_src_stack_alone_is_not_hang_product() -> None:
    """Кадр в product path без разбора функции не даёт HANG_PRODUCT."""
    node = "tests/unit/foo.py::test_slow"
    obs = f"pytest: {node} - PYTEST_STALL"
    evidence = {
        "stall_when": "call",
        "hang_product_eligible": True,
        "stack_status": "captured",
        "stderr_tail": 'File "src/stroy/foo.py", line 10, in bar\n',
    }
    assert not hang_product_proven_from_stall_evidence(evidence)
    assert (
        deterministic_duration_classification(
            observation=obs,
            node_id=node,
            stall_evidence=evidence,
        )
        is None
    )


def test_product_stall_with_function_analysis_is_hang_product(tmp_path: Path) -> None:
    """HANG_PRODUCT только при ``stall_function_analysis.unbounded_wait_proven``."""
    product = tmp_path / "src" / "stroy"
    product.mkdir(parents=True)
    source = product / "hang_module.py"
    _ = source.write_text(
        "def spin():\n    while True:\n        pass\n",
        encoding="utf-8",
    )
    stderr = f'File "{source}", line 2, in spin\n'
    analysis = stall_function_analysis_from_stderr(stderr, tmp_path)
    assert analysis is not None
    assert analysis.get("unbounded_wait_proven") is True
    node = "tests/unit/foo.py::test_slow"
    obs = f"pytest: {node} - PYTEST_STALL"
    evidence = {
        "stall_when": "call",
        "hang_product_eligible": True,
        "stack_status": "captured",
        "stderr_tail": stderr,
        "stall_function_analysis": analysis,
    }
    assert hang_product_proven_from_stall_evidence(evidence)
    assert (
        deterministic_duration_classification(
            observation=obs,
            node_id=node,
            stall_evidence=evidence,
        )
        == "HANG_PRODUCT"
    )


def test_probe_pass_is_personal_timeout() -> None:
    node = "tests/unit/foo.py::test_slow"
    obs = f"pytest: {node} - PYTEST_STALL"
    assert (
        deterministic_duration_classification(
            observation=obs,
            node_id=node,
            personal_timeout_probe={"status": "PASS"},
        )
        == "PERSONAL_TIMEOUT"
    )


def test_exhausted_probe_is_unclear() -> None:
    node = "tests/unit/foo.py::test_slow"
    obs = f"pytest: {node} - PYTEST_STALL"
    assert (
        deterministic_duration_classification(
            observation=obs,
            node_id=node,
            duration_probe_exhausted=True,
        )
        == "UNCLEAR"
    )


def test_unclear_stall_routes_to_probe_once() -> None:
    node = "tests/smoke/test_x.py::test_y"
    obs = f"pytest: {node} - PYTEST_STALL"
    assert (
        duration_route_edge(
            {
                "test_duration_result": {"classification": "UNCLEAR"},
                "dispatcher_current_item": {"observation": obs},
            },
        )
        == "pytest_personal_timeout"
    )
    assert (
        duration_route_edge(
            {
                "test_duration_result": {"classification": "UNCLEAR"},
                "dispatcher_current_item": {"observation": obs},
                "duration_probe_keys": [f"{node}\n{obs}"],
            },
        )
        == "test_finding_finalize"
    )


def test_marker_ceiling_and_suite_extra(tmp_path: Path) -> None:
    assert clamp_personal_timeout_seconds(900) == 300
    tests = tmp_path / "tests" / "unit"
    tests.mkdir(parents=True)
    target = tests / "test_slow.py"
    _ = target.write_text("def test_slow():\n    return None\n", encoding="utf-8")
    assert write_pytest_timeout_marker(tmp_path, "tests/unit/test_slow.py::test_slow", 40)
    text = target.read_text(encoding="utf-8")
    assert "@pytest.mark.timeout(40)" in text
    assert personal_timeout_extra_seconds(tmp_path) == 35
    assert write_pytest_timeout_marker(tmp_path, "tests/unit/test_slow.py::test_slow", 900)
    assert "@pytest.mark.timeout(300)" in target.read_text(encoding="utf-8")
    assert personal_timeout_extra_seconds(tmp_path) == 295
