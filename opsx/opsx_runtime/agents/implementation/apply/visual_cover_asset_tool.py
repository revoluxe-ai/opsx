"""Optional project-specific visual tool, selected by explicit project configuration."""
from __future__ import annotations
import importlib
import os


def _project_module():
    name = os.environ.get("OPSX_PROJECT_VISUAL_ASSET_MODULE", "").strip()
    if not name:
        raise RuntimeError("PROJECT_VISUAL_ASSET_CAPABILITY_UNCONFIGURED")
    return importlib.import_module(name)


def hop_includes_visual_cover_asset_tool(agent_key: str | None) -> bool:
    return bool(os.environ.get("OPSX_PROJECT_VISUAL_ASSET_MODULE")) and str(agent_key or "").strip() == "apply"


def build_visual_cover_asset_custom_tool(*, repo_root):
    return _project_module().build_visual_cover_asset_custom_tool(repo_root=repo_root)


def audit_invest_yalta_cover_bundle(repo_root):
    return _project_module().audit_invest_yalta_cover_bundle(repo_root)
