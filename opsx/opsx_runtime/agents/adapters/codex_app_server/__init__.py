"""Codex app-server provider registration (explicit OPSX_ADAPTER_MODULE selection)."""
from agents.adapters import provider_registry
from .codex_adapter import CodexAppServerAdapter

# Both facade entrypoints stay registered, but mutating tools fail closed.
provider_registry.register_review_adapter_factory(CodexAppServerAdapter)
provider_registry.register_repair_adapter_factory(CodexAppServerAdapter)
provider_registry.register_bootstrap_hook("ensure_wheel_importable", lambda **kwargs: None)

__all__ = ["CodexAppServerAdapter"]
