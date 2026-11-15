"""AsyncClient и launch_bridge для reconcile workflow (единственная точка импорта провайдера)."""

from __future__ import annotations

from typing import Any


async def launch_bridge_client(**launch: Any) -> Any:
    """Запустить ephemeral bridge и вернуть async context manager клиента.

    Args:
        launch: Kwargs для ``AsyncClient.launch_bridge`` (cwd, store, api key).

    Returns:
        Контекстный менеджер async-клиента GetRun.
    """
    client_cls = async_client_type()
    return await client_cls.launch_bridge(**launch)


def async_client_type() -> type:
    """Класс async-клиента провайдера (для unit-тестов и type checks)."""
    from cursor_sdk.asyncio import AsyncClient

    return AsyncClient
