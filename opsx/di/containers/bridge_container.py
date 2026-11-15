"""Shim к colocated ``BridgeContainer`` в ``agents.adapters.cursor_sdk.containers``."""

from opsx.di._runtime_imports import ensure_opsx_runtime_importable

_ = ensure_opsx_runtime_importable()

from agents.adapters.cursor_sdk.containers.bridge_container import (  # noqa: E402
    BridgeContainer,
    _cursor_bridge_async_resource,
    _verify_impl_store_scope,
)

__all__ = [
    "BridgeContainer",
    "_cursor_bridge_async_resource",
    "_verify_impl_store_scope",
]
