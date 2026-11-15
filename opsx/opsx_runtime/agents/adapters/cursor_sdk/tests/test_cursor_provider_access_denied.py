"""Permission denied Cursor SDK → немедленный VerifyStageStop."""

from __future__ import annotations

import pytest

from agents.abc_agent import (
    is_cursor_bridge_launch_failure,
    is_cursor_bridge_stream_drop,
    is_cursor_provider_access_denied,
    raise_cursor_infra_failure,
    raise_cursor_provider_access_denied,
)
from agents.adapters.provider_errors import NetworkError
from workflows.stop_errors import VerifyStageStop


def test_is_cursor_provider_access_denied_by_type_name() -> None:
    class PermissionDeniedError(Exception):
        pass

    assert is_cursor_provider_access_denied(
        PermissionDeniedError("permission_denied: region"),
    )


def test_is_cursor_provider_access_denied_by_message() -> None:
    assert is_cursor_provider_access_denied(RuntimeError("permission_denied: x"))


def test_raise_cursor_provider_access_denied_verify_stop() -> None:
    exc = RuntimeError("permission_denied: Cursor is not available in your region.")
    with pytest.raises(VerifyStageStop) as err:
        raise_cursor_provider_access_denied(
            state={"stream_label": "verify-impl-visual][project_bug"},
            exc=exc,
        )
    assert err.value.code == "CURSOR_PROVIDER_UNAVAILABLE"
    assert err.value.stage == "verify-impl-visual][project_bug"


def test_is_cursor_bridge_launch_failure() -> None:
    class CursorSDKError(Exception):
        pass

    assert is_cursor_bridge_launch_failure(
        CursorSDKError("Timed out waiting for bridge discovery"),
    )


def test_raise_cursor_infra_failure_stream_drop() -> None:
    exc = NetworkError("Bridge request failed: ReadError: ")
    assert is_cursor_bridge_stream_drop(exc)
    with pytest.raises(VerifyStageStop) as err:
        raise_cursor_infra_failure(
            exc,
            state={"stream_label": "verify-impl-visual][project_bug"},
        )
    assert err.value.code == "CURSOR_BRIDGE_UNAVAILABLE"


def test_raise_cursor_infra_failure_bridge() -> None:
    class CursorSDKError(Exception):
        pass

    with pytest.raises(VerifyStageStop) as err:
        raise_cursor_infra_failure(
            CursorSDKError("Timed out waiting for bridge discovery"),
        )
    assert err.value.code == "CURSOR_BRIDGE_UNAVAILABLE"
from agents.adapters.provider_bootstrap import ensure_adapter_factories_registered
ensure_adapter_factories_registered()
