"""DI-контейнеры Cursor SDK (source of truth для bridge lifecycle и адаптеров).

``opsx.di.containers.*`` — thin shim к этому пакету. Открытие bridge::

    container = BridgeContainer()
    container.repo_root.override(path)
    container.change_id.override(change_id)
    await container.init_resources()
    bridge = await container.bridge_runtime()
    await container.shutdown_resources()

Эквивалент ``run_verify_impl``: ``AsyncExitStack`` + ``async with CursorBridgeRuntime(...)``.
См. ``opsx/di/main_container.py`` (`open_bridge` / `close_bridge`) для verify-impl фасада.
"""

from .adapters_container import AdaptersContainer, build_cursor_sdk_adapter_bundle
from .bridge_container import BridgeContainer

__all__ = [
    "AdaptersContainer",
    "BridgeContainer",
    "build_cursor_sdk_adapter_bundle",
]
