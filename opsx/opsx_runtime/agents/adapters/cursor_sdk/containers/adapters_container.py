"""Фабрики ``CursorSDKAdapter`` и ``RepairCursorSDKAdapter`` на dependency-injector."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from dependency_injector import containers, providers

from ..bootstrap import ensure_cursor_sdk_importable
from ..cursor_sdk_adapter import CursorSDKAdapter
from agents.agent_tools.registry import AgentToolRegistry

from ..repair_cursor_sdk_adapter import RepairCursorSDKAdapter
from ..types import CursorSdkAdapterBundle

if TYPE_CHECKING:
    from ..cursor_bridge_runtime import CursorBridgeRuntime


def build_cursor_sdk_adapter_bundle(
    repo_root: Path,
    timeout_seconds: float,
    bridge: CursorBridgeRuntime,
    *,
    allow_git_shell_review: bool = True,
) -> CursorSdkAdapterBundle:
    """Создать пару адаптеров, разделяющих ``bridge_runtime``."""
    ensure_cursor_sdk_importable(repo_root=repo_root)
    review_adapter = CursorSDKAdapter(
        timeout_seconds=timeout_seconds,
        allow_git_shell=allow_git_shell_review,
        bridge_runtime=bridge,
    )
    apply_adapter = RepairCursorSDKAdapter(
        timeout_seconds=timeout_seconds,
        bridge_runtime=bridge,
    )
    apply_adapter.agent_tool_registry = AgentToolRegistry(apply_adapter)
    return CursorSdkAdapterBundle(
        review_adapter=review_adapter,
        apply_adapter=apply_adapter,
    )


class AdaptersContainer(containers.DeclarativeContainer):
    """``Factory`` для review/repair адаптеров (требует открытый bridge снаружи)."""

    config = providers.Configuration()

    repo_root = providers.Object(Path())
    timeout_seconds = providers.Object(60.0)
    allow_git_shell_review = providers.Object(True)

    bundle = providers.Factory(
        build_cursor_sdk_adapter_bundle,
        repo_root=repo_root,
        timeout_seconds=timeout_seconds,
        bridge=providers.Dependency(),
        allow_git_shell_review=allow_git_shell_review,
    )
