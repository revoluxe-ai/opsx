"""Регрессия: adoption SLICE-008 — green hop без edit tool."""

from __future__ import annotations

import json
from pathlib import Path

from agents.implementation.apply_green.agent import _APPLY_GREEN_TOOLS
from agents.implementation.green_hop_product_edit import (
    green_hop_allows_product_edit,
    green_hop_tools_for_slice,
)
from workflows.repo_paths import resolve_opsx_repository_root

_REPO = resolve_opsx_repository_root(Path(__file__).resolve().parents[5])


def _write_json(path: Path, body: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")


def test_visual_slice_008_adoption_disallows_product_edit() -> None:
    """On-disk visual SLICE-008: verify-only green — edit вне tool list."""
    change = "visual"
    slice_id = "SLICE-008"
    assert green_hop_allows_product_edit(change, slice_id, repo_root=_REPO) is False
    tools = green_hop_tools_for_slice(
        _APPLY_GREEN_TOOLS,
        change,
        slice_id,
        repo_root=_REPO,
    )
    assert tools == ("read", "run_pytest")
    assert "edit" not in tools


def test_adoption_fixture_disallows_product_edit(tmp_path: Path) -> None:
    """Изолированная фикстура adoption — edit вне tool list."""
    change = "visual"
    slice_id = "SLICE-008"
    red = {
        "status": "ALREADY_SATISFIED",
        "observation_only": True,
        "implementation_write_authorized": False,
        "test_paths": [
            "tests/unit/channels/telegram/invest_yalta/test_visual_funnel_acceptance.py",
        ],
    }
    impl = {
        "status": "IMPLEMENTATION_ADOPTED",
        "branch": "ADOPTION",
        "production_paths_modified": [],
        "production_paths_created": [],
        "product_files_modified": False,
    }
    _write_json(
        tmp_path / f"openspec/changes/{change}/apply-red/{slice_id}/RED.json",
        red,
    )
    _write_json(
        tmp_path / f"openspec/changes/{change}/apply-implement/{slice_id}/IMPLEMENTATION.json",
        impl,
    )
    (tmp_path / "opsx").mkdir()
    assert green_hop_allows_product_edit(change, slice_id, repo_root=tmp_path) is False


def test_implementation_written_keeps_edit(tmp_path: Path) -> None:
    """Нормальный green с product delta — edit остаётся."""
    change = "visual"
    slice_id = "SLICE-004"
    _write_json(
        tmp_path / f"openspec/changes/{change}/apply-red/{slice_id}/RED.json",
        {"status": "ALREADY_SATISFIED", "observation_only": False},
    )
    _write_json(
        tmp_path / f"openspec/changes/{change}/apply-implement/{slice_id}/IMPLEMENTATION.json",
        {
            "status": "IMPLEMENTATION_WRITTEN",
            "production_paths_modified": ["src/stroy/channels/max/invest_yalta/max_present.py"],
        },
    )
    (tmp_path / "opsx").mkdir()
    assert green_hop_allows_product_edit(change, slice_id, repo_root=tmp_path) is True