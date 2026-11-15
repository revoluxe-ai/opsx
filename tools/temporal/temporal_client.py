"""Shared helpers for Temporal connectivity in the POC."""

from __future__ import annotations

import asyncio

from config import TEMPORAL_ADDRESS, TEMPORAL_UI_URL


def print_temporal_startup_help(error: Exception | None = None) -> None:
    print("STATUS: TEMPORAL_SERVER_UNAVAILABLE")
    print(f"MESSAGE: Cannot connect to {TEMPORAL_ADDRESS}")
    if error is not None:
        print(f"DETAIL: {type(error).__name__}: {error}")
    print("HOW_TO_START:")
    print("  1) Start Docker Desktop, then:")
    print("     cd tools/temporal")
    print("     docker compose up -d")
    print("  2) Wait until UI opens:")
    print(f"     {TEMPORAL_UI_URL}")
    print("  3) Then rerun worker/start in two terminals.")


async def connect_temporal_client():
    from temporalio.client import Client

    last_error: Exception | None = None
    for attempt in range(1, 16):
        try:
            return await Client.connect(TEMPORAL_ADDRESS)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < 15:
                await asyncio.sleep(2)
                continue
            print_temporal_startup_help(last_error)
            raise SystemExit(2) from last_error

    print_temporal_startup_help(last_error)
    raise SystemExit(2)
