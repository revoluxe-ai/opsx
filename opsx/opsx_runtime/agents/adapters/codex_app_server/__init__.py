"""Codex app-server provider registration (explicit OPSX_ADAPTER_MODULE selection)."""
from agents.adapters import provider_registry
from .codex_adapter import CodexAppServerAdapter
from .execution_session import CodexExecutionSession

# Both facade entrypoints stay registered, but mutating tools fail closed.
provider_registry.register_review_adapter_factory(
    CodexAppServerAdapter, provider_module="agents.adapters.codex_app_server",
)
provider_registry.register_repair_adapter_factory(
    CodexAppServerAdapter, provider_module="agents.adapters.codex_app_server",
)
provider_registry.register_bootstrap_hook(
    "ensure_wheel_importable", lambda **kwargs: None,
    provider_module="agents.adapters.codex_app_server",
)
provider_registry.register_execution_session_factory(
    "agents.adapters.codex_app_server", CodexExecutionSession,
)
from .execution_workspace import CodexExecutionWorkspace
provider_registry.register_execution_workspace_factory(
    "agents.adapters.codex_app_server", CodexExecutionWorkspace,
)

__all__ = ["CodexAppServerAdapter"]
