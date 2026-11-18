"""Пакет адаптеров Cursor SDK для opsx_runtime.

Содержит транспорт verify (``CursorSDKAdapter``), общий bridge-runtime,
live-log стрима и минимальный probe. Старые пути ``agents.adapters.cursor_*``
остаются shim-реэкспортами для обратной совместимости.
"""

from .cursor_bridge_runtime import CursorBridgeRuntime
from .cursor_sdk_adapter import CursorSDKAdapter, REPO_ROOT, _token_counts
from .cursor_sdk_stream import (
    CURSOR_HOP_CANCEL_DRAIN_SECONDS,
    CURSOR_HOP_HARD_DEADLINE_SECONDS,
    CURSOR_STREAM_GETRUN_POLL_SECONDS,
    CURSOR_STREAM_GETRUN_RPC_TIMEOUT_SECONDS,
    CURSOR_STREAM_IDLE_TIMEOUT_SECONDS,
    CURSOR_STREAM_WAIT_STATE_INTERVAL_SECONDS,
    compose_agent_stream_label,
    configure_cursor_sdk_stream_logger,
    conversation_path_from_stream_log,
    format_live_line,
    live_stream_log_level,
    log_cursor_hop_primary_exception,
    log_cursor_stream_stalled_on_unknown,
    stream_debug_unknown_tally,
    wait_cursor_run_with_live_log,
)
from .cursor_sdk_stream_minimal import run_minimal_probe
from .repair_cursor_sdk_adapter import (
    ALLOWED_TOOLS,
    MUTATION_TOOLS,
    READ_ONLY_TOOLS,
    RepairCursorSDKAdapter,
    default_hop_model_selection,
    implementation_arbiter_model_selection,
    model_selection_for_sdk,
    tools_for_cursor_sdk_create,
)
from agents.adapters import provider_registry

# Explicit registration fixes provider authority even when other modules are imported.
_PROVIDER_MODULE = "agents.adapters.cursor_sdk"


def _register_review(factory):
    return provider_registry.register_review_adapter_factory(
        factory, provider_module=_PROVIDER_MODULE,
    )


def _register_repair(factory):
    return provider_registry.register_repair_adapter_factory(
        factory, provider_module=_PROVIDER_MODULE,
    )


def _register_bootstrap(name, hook):
    return provider_registry.register_bootstrap_hook(
        name, hook, provider_module=_PROVIDER_MODULE,
    )


def _register_reconcile(name, hook):
    return provider_registry.register_reconcile_hook(
        name, hook, provider_module=_PROVIDER_MODULE,
    )


from .containers import AdaptersContainer, BridgeContainer, build_cursor_sdk_adapter_bundle
from .reconcile_async_client import async_client_type, launch_bridge_client
from .reconcile_billing import refresh_saved_run_billing
from .wheel_bootstrap import ensure_cursor_sdk_importable

_register_review(CursorSDKAdapter)
_register_repair(RepairCursorSDKAdapter)
_register_bootstrap(
    "ensure_wheel_importable", ensure_cursor_sdk_importable
)
_register_bootstrap("token_counts_from_usage", _token_counts)
_register_reconcile("launch_bridge_client", launch_bridge_client)
_register_reconcile("async_client_type", async_client_type)
_register_reconcile("bridge_runtime_type", lambda: CursorBridgeRuntime)

from .execution_session import CursorExecutionSession
provider_registry.register_execution_session_factory(
    "agents.adapters.cursor_sdk", CursorExecutionSession,
)
from .execution_workspace import CursorExecutionWorkspace
provider_registry.register_execution_workspace_factory(
    "agents.adapters.cursor_sdk", CursorExecutionWorkspace,
)
_register_reconcile(
    "refresh_saved_run_billing", refresh_saved_run_billing
)
_register_reconcile("stream_debug_unknown_tally", stream_debug_unknown_tally)
_register_reconcile("run_minimal_probe", run_minimal_probe)

__all__ = [
    "CURSOR_HOP_CANCEL_DRAIN_SECONDS",
    "CURSOR_HOP_HARD_DEADLINE_SECONDS",
    "CURSOR_STREAM_GETRUN_POLL_SECONDS",
    "CURSOR_STREAM_GETRUN_RPC_TIMEOUT_SECONDS",
    "CURSOR_STREAM_IDLE_TIMEOUT_SECONDS",
    "CURSOR_STREAM_WAIT_STATE_INTERVAL_SECONDS",
    "CursorBridgeRuntime",
    "CursorSDKAdapter",
    "REPO_ROOT",
    "_token_counts",
    "compose_agent_stream_label",
    "configure_cursor_sdk_stream_logger",
    "conversation_path_from_stream_log",
    "format_live_line",
    "live_stream_log_level",
    "log_cursor_hop_primary_exception",
    "log_cursor_stream_stalled_on_unknown",
    "run_minimal_probe",
    "wait_cursor_run_with_live_log",
    "ALLOWED_TOOLS",
    "MUTATION_TOOLS",
    "READ_ONLY_TOOLS",
    "AdaptersContainer",
    "BridgeContainer",
    "RepairCursorSDKAdapter",
    "build_cursor_sdk_adapter_bundle",
    "default_hop_model_selection",
    "implementation_arbiter_model_selection",
    "model_selection_for_sdk",
    "tools_for_cursor_sdk_create",
]

from .failure_classification import classify_failure
_register_bootstrap("classify_failure", classify_failure)
from .capability_options import custom_tool_specs_to_provider as _convert_custom_tools
_register_bootstrap("custom_tool_specs_to_provider", _convert_custom_tools)
