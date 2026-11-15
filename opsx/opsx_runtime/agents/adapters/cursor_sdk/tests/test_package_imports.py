"""Smoke: colocated cursor_sdk package и workflow neutral ports."""

from __future__ import annotations


def test_cursor_sdk_package_exports() -> None:
    from agents.adapters.cursor_sdk import (
        BridgeContainer,
        CursorBridgeRuntime,
        CursorSDKAdapter,
        RepairCursorSDKAdapter,
    )

    assert BridgeContainer is not None
    assert CursorBridgeRuntime is not None
    assert CursorSDKAdapter is not None
    assert RepairCursorSDKAdapter is not None


def test_workflow_repair_port_imports_repair_adapter() -> None:
    from workflows.repair.repair_adapter_port import RepairAdapter

    from agents.adapters.cursor_sdk.repair_cursor_sdk_adapter import (
        RepairCursorSDKAdapter,
    )

    assert RepairAdapter.__name__ == "RepairAdapter"
    assert RepairCursorSDKAdapter.__name__ == "RepairCursorSDKAdapter"


def test_opsx_di_bridge_shim_imports_bridge_container() -> None:
    from opsx.di.containers.bridge_container import BridgeContainer as DiBridge
    from agents.adapters.cursor_sdk.containers.bridge_container import BridgeContainer

    assert DiBridge is BridgeContainer
