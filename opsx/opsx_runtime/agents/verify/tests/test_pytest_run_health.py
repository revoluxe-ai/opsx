"""Тесты pytest_run_health fast-path."""

from agents.verify.pytest_run_health import (
    classify_pytest_observation_fast,
    is_pytest_harness_observation,
    scope_key_from_modules,
)


def test_harness_syntax_observation() -> None:
    obs = "pytest: SyntaxError in tests/unit/test_foo.py"
    assert is_pytest_harness_observation(obs)
    assert classify_pytest_observation_fast(obs) == "BUILD"


def test_failed_node_is_test() -> None:
    obs = "pytest: FAILED tests/unit/test_foo.py::test_bar"
    assert not is_pytest_harness_observation(obs)
    assert classify_pytest_observation_fast(obs) == "TEST"


def test_scope_key_sorted() -> None:
    assert scope_key_from_modules(["tests/b.py", "tests/a.py"]) == "tests/a.py|tests/b.py"
