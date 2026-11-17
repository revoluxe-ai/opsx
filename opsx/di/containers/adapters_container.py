"""Shim: фабрики адаптеров в ``agents.adapters.cursor_sdk.containers`` → ``VerifyImplAdapters``."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from dependency_injector import containers, providers

from opsx.di._runtime_imports import ensure_opsx_runtime_importable

_ = ensure_opsx_runtime_importable()

from agents.adapters.cursor_sdk.containers.adapters_container import (  # noqa: E402
    build_cursor_sdk_adapter_bundle,
)
from opsx.di.types import VerifyImplAdapters

if TYPE_CHECKING:
    from agents.adapters.cursor_bridge_runtime import CursorBridgeRuntime


def _build_verify_impl_adapters(
    repo_root: Path,
    timeout_seconds: float,
    bridge: CursorBridgeRuntime,
    *,
    allow_git_shell_review: bool = False,
) -> VerifyImplAdapters:
    """Обёртка colocated bundle для composition root ``opsx.di``."""
    bundle = build_cursor_sdk_adapter_bundle(
        repo_root,
        timeout_seconds,
        bridge,
        allow_git_shell_review=allow_git_shell_review,
    )
    return VerifyImplAdapters(
        review_adapter=bundle.review_adapter,
        apply_adapter=bundle.apply_adapter,
    )


class AdaptersContainer(containers.DeclarativeContainer):
    """``Factory`` для ``CursorSDKAdapter`` и ``RepairCursorSDKAdapter`` (DI verify-impl)."""

    config = providers.Configuration()

    repo_root = providers.Object(Path())
    timeout_seconds = providers.Object(60.0)
    allow_git_shell_review = providers.Object(False)

    bundle = providers.Factory(
        _build_verify_impl_adapters,
        repo_root=repo_root,
        timeout_seconds=timeout_seconds,
        bridge=providers.Dependency(),
        allow_git_shell_review=allow_git_shell_review,
    )
