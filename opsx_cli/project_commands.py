"""Product project configuration owned by the consumer repository."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .context import Project
from .storage import read_json


async def project_show(project: Project) -> dict[str, Any]:
    settings = await read_json(project.settings, {})
    cap_rel = settings.get("capabilities", "opsx/project/capabilities.yaml")
    cap_path = project.root / cap_rel
    body = cap_path.read_text(encoding="utf-8") if cap_path.is_file() else ""
    return {
        "project_id": settings.get("project_id"),
        "name": settings.get("name") or project.root.name,
        "capabilities_path": cap_rel,
        "capabilities_present": cap_path.is_file(),
        "capabilities_excerpt": body[:2000],
        "adapter": settings.get("adapter"),
    }


async def project_validate(project: Project) -> dict[str, Any]:
    settings = await read_json(project.settings, {})
    cap_rel = settings.get("capabilities", "opsx/project/capabilities.yaml")
    cap_path = project.root / cap_rel
    checks = []
    checks.append(("capabilities_file", cap_path.is_file()))
    adoption_ready = False
    if cap_path.is_file():
        text = cap_path.read_text(encoding="utf-8")
        adoption_ready = "status: NEEDS_ADOPTION" not in text
        checks.append(("adoption_status", adoption_ready))
        checks.append(("adapter_declared", "adapter:" in text))
    pyproject = project.root / "pyproject.toml"
    checks.append(("product_pyproject", pyproject.is_file()))
    return {
        "status": "PASS" if all(ok for _, ok in checks) else "NOT_READY",
        "checks": [{"name": n, "ok": ok} for n, ok in checks],
    }