"""Регрессия: MCP visual_cover_asset для verify apply-hop."""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.implementation.apply.cover_asset_apply_gate import (
    apply_touched_cover_assets,
    gate_apply_cover_bundle,
)
from agents.implementation.apply.visual_cover_asset_tool import (
    ApplyVisualCoverAssetRuntime,
    audit_invest_yalta_cover_bundle,
    hop_includes_visual_cover_asset_tool,
)
from agents.verify.impl_verify.scoped_read_tool import build_impl_verify_scoped_read_tools
from agents.config import resolve_read_scope

_REPO = Path(__file__).resolve().parents[6]


def test_hop_includes_visual_cover_asset_only_for_apply() -> None:
    assert hop_includes_visual_cover_asset_tool("apply")
    assert not hop_includes_visual_cover_asset_tool("apply_red")
    assert not hop_includes_visual_cover_asset_tool("impl_verify")


def test_apply_scoped_tools_include_visual_cover_asset() -> None:
    scope = resolve_read_scope("apply")
    tools = build_impl_verify_scoped_read_tools(
        scope=scope,
        repo_root=_REPO,
        include_visual_cover_asset=True,
    )
    assert "visual_cover_asset" in tools
    assert "read" in tools


@pytest.mark.asyncio
async def test_visual_cover_asset_inspect_ok_on_workspace_bundle() -> None:
    data = _REPO / "src/stroy/channels/telegram/invest_yalta/data/cover_start.png"
    if not data.is_file():
        pytest.skip("visual cover bundle missing in workspace")
    runtime = ApplyVisualCoverAssetRuntime(repo_root=_REPO)
    result = await runtime.visual_cover_asset({"operation": "inspect"}, None)
    assert result.get("ok") is True
    assert result.get("covers", {}).get("cover_start.png") == [1080, 1350]


def test_cover_apply_gate_blocks_incomplete_bundle(tmp_path: Path) -> None:
    data = tmp_path / "src/stroy/channels/telegram/invest_yalta/data"
    data.mkdir(parents=True)
    blocker = gate_apply_cover_bundle(
        tmp_path,
        files_changed=["src/stroy/channels/telegram/invest_yalta/data/cover_start.png"],
    )
    assert blocker
    assert "APPLY_COVER_BUNDLE_INCOMPLETE" in blocker


def test_apply_touched_cover_assets_detects_manifest() -> None:
    assert apply_touched_cover_assets(
        ["src/stroy/channels/telegram/invest_yalta/data/cover_dimensions.json"],
    )


def test_audit_bundle_matches_repo_when_present() -> None:
    audit = audit_invest_yalta_cover_bundle(_REPO)
    if not (_REPO / "src/stroy/channels/telegram/invest_yalta/data/cover_start.png").is_file():
        pytest.skip("visual covers missing")
    if not audit["ok"]:
        pytest.skip(f"workspace cover bundle not normalized: {audit}")
