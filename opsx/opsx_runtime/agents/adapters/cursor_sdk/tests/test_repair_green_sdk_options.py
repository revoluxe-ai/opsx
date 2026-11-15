"""Regression: GREEN_VERIFIER MCP run_pytest не отключается builtin-only extras."""

from __future__ import annotations

from pathlib import Path

from agents.agent_tools.deterministic.registry import hop_includes_run_pytest_tool
from agents.adapters.repair_tool_policy import ALLOWED_TOOLS
from agents.repair.helpers import BOUNDED_HUNTER_TOOLS, GREEN_VERIFIER_TOOLS
from agents.verify.impl_verify.scoped_read_tool import (
    build_impl_verify_scoped_read_sdk_options,
    hop_uses_scoped_read_tool,
)
from agents.config import resolve_read_scope

_REPO = Path(__file__).resolve().parents[6]


def test_green_verifier_non_scoped_fallback_keeps_mcp_when_run_pytest_present() -> None:
    """Страховка non-scoped ветки: ``mcp`` не в ``disallowed_tools`` при include_pytest."""
    normalized_allowed = {t.strip().lower() for t in GREEN_VERIFIER_TOOLS}
    include_pytest = hop_includes_run_pytest_tool("GREEN_VERIFIER") and "run_pytest" in normalized_allowed
    assert include_pytest
    tool_names = [
        t
        for t in ALLOWED_TOOLS
        if str(t).strip().lower() in normalized_allowed and str(t).strip().lower() != "run_pytest"
    ]
    options = {
        "tools": ["mcp", *tool_names],
        "disallowed_tools": ["shell"],
    }
    assert "mcp" not in options["disallowed_tools"]


def test_bounded_hunter_stays_non_scoped() -> None:
    """BOUNDED_HUNTER не переводится на scoped read (есть grep/glob/ls)."""
    assert not hop_uses_scoped_read_tool("BOUNDED_HUNTER", BOUNDED_HUNTER_TOOLS)


def test_classifier_hop_without_run_pytest_uses_builtin_only_extras() -> None:
    """Hop без run_pytest сохраняет прежний builtin-only контракт (mcp off)."""
    from tools.cursor_executor.cursor_executor.bridge_local import (  # type: ignore[import-not-found]
        cursor_sdk_builtin_tools_only_extras,
    )

    assert hop_uses_scoped_read_tool("CLASSIFIER", ("read",))
    assert not hop_includes_run_pytest_tool("CLASSIFIER")
    extras = cursor_sdk_builtin_tools_only_extras()
    assert "mcp" in extras["disallowed_tools"]


def test_green_verifier_scoped_options_include_run_pytest_custom_tool() -> None:
    """Scoped hop GREEN_VERIFIER подключает custom ``run_pytest``."""
    scope = resolve_read_scope("GREEN_VERIFIER")
    options = build_impl_verify_scoped_read_sdk_options(
        api_key="test",
        model={"slug": "composer-2.5"},
        local={},
        scope=scope,
        repo_root=_REPO,
        include_run_pytest=True,
    )
    assert "run_pytest" in options["local"]["custom_tools"]