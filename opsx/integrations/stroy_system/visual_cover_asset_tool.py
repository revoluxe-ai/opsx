"""MCP ``visual_cover_asset`` для verify apply-hop: правка Owner PNG без shell.

Нормализация IHDR 1080×1350 и манифест — через product
``cover_asset_dimensions``; иллюстрации из промпта — заглушка адаптера генерации.
"""

from __future__ import annotations

from opsx.project_context import resource_path

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agents.adapters.agent_capability_options import AgentCustomToolSpec

_TOOL_NAME = "visual_cover_asset"
_DATA_REL = Path("src/stroy/channels/telegram/invest_yalta/data")
_MANIFEST_NAME = "cover_dimensions.json"


def hop_includes_visual_cover_asset_tool(agent_key: str | None) -> bool:
    """True только для verify ``apply`` (техническое обновление обложек, не ImageAgent)."""
    return str(agent_key or "").strip() == "apply"


def _mcp_payload(**fields: Any) -> dict[str, Any]:
    text = json.dumps(fields, ensure_ascii=False, indent=2)
    return {"content": [{"type": "text", "text": text}], **fields}


def _repo_data_dir(repo_root: Path) -> Path:
    return (resource_path(repo_root, _DATA_REL)).resolve()


def _validate_registry_filename(filename: str) -> str:
    from stroy.channels.telegram.invest_yalta.visual_covers import COVER_FILENAMES

    name = str(filename or "").strip().replace("\\", "/")
    if "/" in name or ".." in name:
        msg = "filename must be a bare registry entry"
        raise ValueError(msg)
    if name not in COVER_FILENAMES:
        msg = f"filename not in COVER_FILENAMES: {name!r}"
        raise ValueError(msg)
    return name


def audit_invest_yalta_cover_bundle(repo_root: Path) -> dict[str, Any]:
    """Machine gate: все registry PNG на диске 1080×1350 + ``cover_dimensions.json``."""
    from stroy.channels.telegram.invest_yalta.cover_asset_dimensions import (
        TARGET_SIZE,
        read_png_size,
    )
    from stroy.channels.telegram.invest_yalta.visual_covers import COVER_FILENAMES

    data_dir = _repo_data_dir(repo_root)
    covers: dict[str, list[int]] = {}
    bad: list[str] = []
    missing: list[str] = []
    for name in COVER_FILENAMES:
        path = data_dir / name
        if not path.is_file():
            missing.append(name)
            continue
        width, height = read_png_size(path)
        covers[name] = [width, height]
        if (width, height) != TARGET_SIZE:
            bad.append(f"{name}={width}x{height}")
    manifest = data_dir / _MANIFEST_NAME
    manifest_ok = manifest.is_file()
    manifest_matches = False
    if manifest_ok:
        try:
            payload = json.loads(manifest.read_text(encoding="utf-8"))
            manifest_covers = payload.get("covers") if isinstance(payload, dict) else None
            manifest_matches = isinstance(manifest_covers, dict) and all(
                manifest_covers.get(name) == [TARGET_SIZE[0], TARGET_SIZE[1]]
                for name in COVER_FILENAMES
                if name not in missing
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            manifest_matches = False
    ok = not bad and not missing and manifest_ok and manifest_matches
    return {
        "ok": ok,
        "target_width": TARGET_SIZE[0],
        "target_height": TARGET_SIZE[1],
        "covers": covers,
        "bad_dimensions": bad,
        "missing": missing,
        "manifest_path": str((_DATA_REL / _MANIFEST_NAME).as_posix()),
        "manifest_present": manifest_ok,
        "manifest_matches_ihdr": manifest_matches,
    }


def _sync_inspect(repo_root: Path) -> dict[str, Any]:
    audit = audit_invest_yalta_cover_bundle(repo_root)
    return {**audit, "operation": "inspect"}


def _sync_normalize_registry(repo_root: Path) -> dict[str, Any]:
    from stroy.channels.telegram.invest_yalta.cover_asset_dimensions import (
        ensure_cover_assets_dimensions,
    )

    changed = ensure_cover_assets_dimensions(force=True)
    audit = audit_invest_yalta_cover_bundle(repo_root)
    return {
        **audit,
        "operation": "normalize_registry",
        "changed_filenames": changed,
        "saved": audit["ok"],
    }


def _sync_normalize_one(repo_root: Path, filename: str) -> dict[str, Any]:
    from stroy.channels.telegram.invest_yalta.cover_asset_dimensions import (
        TARGET_SIZE,
        read_png_size,
        resize_png_cover_fit,
        write_cover_dimensions_manifest,
    )

    name = _validate_registry_filename(filename)
    path = _repo_data_dir(repo_root) / name
    if not path.is_file():
        return _mcp_payload(
            ok=False,
            code="MISSING_FILE",
            operation="normalize_one",
            filename=name,
            path=str((_DATA_REL / name).as_posix()),
        )
    size_before = list(read_png_size(path))
    resized = False
    if tuple(size_before) != TARGET_SIZE:
        resize_png_cover_fit(path)
        resized = True
    size_after = list(read_png_size(path))
    write_cover_dimensions_manifest(_repo_data_dir(repo_root))
    audit = audit_invest_yalta_cover_bundle(repo_root)
    return {
        **audit,
        "operation": "normalize_one",
        "filename": name,
        "resized": resized,
        "size_before": size_before,
        "size_after": size_after,
        "saved": size_after == [TARGET_SIZE[0], TARGET_SIZE[1]],
    }


class ApplyVisualCoverAssetRuntime:
    """Исполнитель custom MCP ``visual_cover_asset`` (verify apply-hop)."""

    def __init__(self, *, repo_root: Path) -> None:
        self._repo_root = repo_root.resolve()

    async def visual_cover_asset(self, args: Mapping[str, Any], _ctx: Any) -> dict[str, Any]:
        """inspect | normalize_registry | normalize_one | generate_illustration (stub)."""
        if not isinstance(args, Mapping):
            return _mcp_payload(ok=False, code="INVALID_ARGS")
        operation = str(args.get("operation") or "").strip()
        if operation == "inspect":
            result = await asyncio.to_thread(_sync_inspect, self._repo_root)
            return _mcp_payload(**result)
        if operation == "normalize_registry":
            result = await asyncio.to_thread(_sync_normalize_registry, self._repo_root)
            return _mcp_payload(**result)
        if operation == "normalize_one":
            try:
                filename = _validate_registry_filename(str(args.get("filename") or ""))
            except ValueError as exc:
                return _mcp_payload(ok=False, code="INVALID_FILENAME", error=str(exc))
            result = await asyncio.to_thread(_sync_normalize_one, self._repo_root, filename)
            return _mcp_payload(**result)
        if operation == "generate_illustration":
            return _mcp_payload(
                ok=False,
                code="GENERATION_ADAPTER_NOT_CONFIGURED",
                operation=operation,
                message=(
                    "Illustration generation is not available in apply-hop. "
                    "Use Owner-approved art or a dedicated ImageAgent for composition work."
                ),
            )
        return _mcp_payload(
            ok=False,
            code="INVALID_OPERATION",
            allowed=["inspect", "normalize_registry", "normalize_one", "generate_illustration"],
        )


_VISUAL_COVER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["operation"],
    "properties": {
        "operation": {
            "type": "string",
            "enum": [
                "inspect",
                "normalize_registry",
                "normalize_one",
                "generate_illustration",
            ],
            "description": (
                "inspect: IHDR audit + manifest; normalize_registry: all seven PNGs; "
                "normalize_one: single registry filename; "
                "generate_illustration: reserved (adapter not configured)."
            ),
        },
        "filename": {
            "type": "string",
            "description": "Required for normalize_one (e.g. cover_start.png).",
        },
        "design_hint": {
            "type": "string",
            "description": "Advisory only; generation adapter not wired in apply-hop.",
        },
    },
}


def build_visual_cover_asset_custom_tool(*, repo_root: Path) -> dict[str, AgentCustomToolSpec]:
    """Вернуть map с одним custom tool ``visual_cover_asset``."""
    runtime = ApplyVisualCoverAssetRuntime(repo_root=repo_root)
    return {
        _TOOL_NAME: AgentCustomToolSpec(
            description=(
                "Owner cover PNG tool for invest_yalta/data (change visual task 1.1). "
                "Normalize bundled covers to 1080×1350 without shell; inspect writes "
                "readable manifest evidence. Do not use for creative composition — "
                "that belongs to ImageAgent / Owner art."
            ),
            input_schema=_VISUAL_COVER_SCHEMA,
            execute=runtime.visual_cover_asset,
        ),
    }
