"""Durable per-project model-provider selection for the OPSX control panel.

A selection is an explicit grant for NEW runs. Existing executions carry
their own provider identity; changing the file cannot rewrite run authority.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .context import OpsxError, Project
from .settings import instance_layer
from .storage import _atomic_bytes

PROVIDER_MODULES = {
    "cursor": "agents.adapters.cursor_sdk",
    "codex": "agents.adapters.codex_app_server",
}


def validate_provider(provider: str) -> str:
    if provider not in PROVIDER_MODULES:
        raise OpsxError("EXECUTION_PROVIDER_UNSUPPORTED", "Select cursor or codex")
    return provider


def provider_from_settings(settings: dict[str, Any]) -> str:
    instance = instance_layer(settings)
    if not isinstance(instance, dict):
        raise OpsxError("EXECUTION_PROVIDER_CONFIG_INVALID", "Project instance settings must be an object")
    provider = validate_provider(str(instance.get("provider") or "cursor"))
    adapter = instance.get("adapter") or {}
    if not isinstance(adapter, dict):
        raise OpsxError("EXECUTION_PROVIDER_CONFIG_INVALID", "Adapter settings must be an object")
    module = adapter.get("module")
    if module and module != PROVIDER_MODULES[provider]:
        raise OpsxError("EXECUTION_PROVIDER_CONFIG_CONFLICT",
                        "provider and adapter.module disagree; no automatic substitution")
    return provider


def load_provider(project: Project) -> dict[str, str]:
    """Read exact persisted provider + revision; fail if install settings are absent."""
    try:
        raw = project.settings.read_bytes()
        parsed = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise OpsxError("EXECUTION_PROVIDER_CONFIG_MISSING", "Project settings missing or invalid") from exc
    if not isinstance(parsed, dict):
        raise OpsxError("EXECUTION_PROVIDER_CONFIG_INVALID", "Project settings must be an object")
    provider = provider_from_settings(parsed)
    return {
        "provider": provider,
        "model": "gpt-6-luna" if provider == "codex" else "per-role Cursor policy",
        "revision": hashlib.sha256(raw).hexdigest(),
    }


def save_provider(project: Project, *, provider: str, expected_revision: str) -> dict[str, str]:
    """Atomic versioned update, preserving all unrelated project configuration."""
    validate_provider(provider)
    if not isinstance(expected_revision, str) or len(expected_revision) != 64:
        raise OpsxError("EXECUTION_PROVIDER_REVISION_REQUIRED", "Supply the current configuration revision")
    lock = project.runs / "provider-settings.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise OpsxError("EXECUTION_PROVIDER_CONFIG_LOCKED", "Provider configuration is being modified") from exc
        try:
            current = load_provider(project)
            if current["revision"] != expected_revision:
                raise OpsxError("EXECUTION_PROVIDER_REVISION_STALE", "Project settings changed; reload before saving")
            document = json.loads(project.settings.read_text(encoding="utf-8"))
            if document.get("schema_version", 1) >= 2 and isinstance(document.get("instance"), dict):
                layer = document["instance"]
            elif document.get("schema_version", 1) == 1:
                layer = document
            else:
                raise OpsxError("EXECUTION_PROVIDER_CONFIG_INVALID", "Unknown project configuration schema")
            adapter = layer.get("adapter") or {}
            if not isinstance(adapter, dict):
                raise OpsxError("EXECUTION_PROVIDER_CONFIG_INVALID", "Adapter settings must be an object")
            adapter["module"] = PROVIDER_MODULES[provider]
            layer["adapter"] = adapter
            layer["provider"] = provider
            # Never alter extras, dependencies, product profile or unrelated instance settings.
            _atomic_bytes(
                project.settings,
                (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
            )
        finally:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    return load_provider(project)
