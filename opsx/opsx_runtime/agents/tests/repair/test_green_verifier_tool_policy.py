"""Tool policy: GREEN_VERIFIER получает run_pytest через capability, не shell."""

from __future__ import annotations

from pathlib import Path

from agents.agent_tools.deterministic.registry import hop_includes_run_pytest_tool
from agents.config import resolve_read_scope
from agents.repair.helpers import GREEN_VERIFIER_TOOLS
from agents.verify.impl_verify.scoped_read_tool import (
    build_impl_verify_scoped_read_sdk_options,
    hop_uses_scoped_read_tool,
)
from workflows.repair.checks import red_green_identity_errors
from workflows.repair.schemas import GreenEvidence, RedEvidence

_REPO = Path(__file__).resolve().parents[5]


def test_green_verifier_hop_includes_run_pytest() -> None:
    assert hop_includes_run_pytest_tool("GREEN_VERIFIER")


def test_green_verifier_tool_tuple_is_read_plus_run_pytest() -> None:
    assert GREEN_VERIFIER_TOOLS == ("read", "run_pytest")


def test_green_verifier_sdk_scoped_options_contract() -> None:
    """Контракт SDK: scoped read + MCP run_pytest для GREEN_VERIFIER."""
    assert hop_uses_scoped_read_tool("GREEN_VERIFIER", GREEN_VERIFIER_TOOLS)
    scope = resolve_read_scope("GREEN_VERIFIER")
    options = build_impl_verify_scoped_read_sdk_options(
        api_key="test",
        model={"slug": "composer-2.5"},
        local={},
        scope=scope,
        repo_root=_REPO,
        include_run_pytest=True,
    )
    assert "mcp" in options["tools"]
    assert "mcp" not in options["disallowed_tools"]
    assert "run_pytest" in options["local"]["custom_tools"]


def test_green_status_blocked_yields_identity_error() -> None:
    red = RedEvidence(
        status="RED_CONFIRMED",
        test_id="tests/unit/test_x.py::test_y",
        command="poetry run python -m pytest tests/unit/test_x.py::test_y",
        expected_failure="AssertionError",
        actual_failure="AssertionError",
        failure_match=True,
    )
    green = GreenEvidence(
        status="BLOCKED",
        test_id="tests/unit/test_x.py::test_y",
    )
    errors = red_green_identity_errors(
        red,
        green,
        red_test_content_identity=None,
        current_test_content_identity=None,
    )
    assert errors == ["GREEN_STATUS_BLOCKED"]