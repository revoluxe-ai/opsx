"""OPSX installation metadata and instance settings (read-only validation surface)."""
from __future__ import annotations

from typing import Any

from .context import Project
from .storage import read_json


async def config_show(project: Project, *, section: str | None = None) -> dict[str, Any]:
    installation = await read_json(project.installation, {})
    settings = await read_json(project.settings, {})
    payload = {
        "installation_metadata": installation,
        "instance_config": settings,
        "sections": {
            "installation_metadata": "Pinned core, managed files, runtime identity",
            "instance_config": "Provider, adapter, workflow defaults in opsx/project/project.json",
        },
    }
    if section == "installation":
        return {"section": section, "data": installation}
    if section == "instance":
        return {"section": section, "data": settings}
    if section:
        return {"section": section, "error": "unknown_section"}
    return payload


async def config_validate(project: Project) -> dict[str, Any]:
    installation = await read_json(project.installation, {})
    settings = await read_json(project.settings, {})
    issues: list[str] = []
    if not installation:
        issues.append("installation_metadata_missing")
    else:
        if installation.get("schema_version") != 1:
            issues.append("installation_schema_unsupported")
        if not installation.get("core_sha"):
            issues.append("core_sha_missing")
    if not settings:
        issues.append("instance_config_missing")
    elif settings.get("schema_version") != 1:
        issues.append("instance_schema_unsupported")
    return {"status": "PASS" if not issues else "NOT_READY", "issues": issues}