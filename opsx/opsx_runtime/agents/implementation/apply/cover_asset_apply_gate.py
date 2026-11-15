"""Post-apply machine check: invest_yalta cover bundle готов к повторному impl_verify."""

from __future__ import annotations

from pathlib import Path

from agents.implementation.apply.visual_cover_asset_tool import audit_invest_yalta_cover_bundle

_COVER_DATA_PREFIX = "src/stroy/channels/telegram/invest_yalta/data/"


def apply_touched_cover_assets(files_changed: list[str]) -> bool:
    """True, если apply-hop затронул bundled cover PNG или манифест."""
    for raw in files_changed:
        path = str(raw or "").replace("\\", "/")
        if not path.startswith(_COVER_DATA_PREFIX):
            continue
        base = path.rsplit("/", 1)[-1]
        if base.startswith("cover_") and base.endswith(".png"):
            return True
        if base == "cover_dimensions.json":
            return True
    return False


def gate_apply_cover_bundle(repo_root: Path, *, files_changed: list[str]) -> str | None:
    """Вернуть текст блокера или None, если IHDR evidence достаточно."""
    if not apply_touched_cover_assets(files_changed):
        return None
    audit = audit_invest_yalta_cover_bundle(repo_root)
    if audit.get("ok"):
        return None
    bad = audit.get("bad_dimensions") or []
    missing = audit.get("missing") or []
    parts: list[str] = ["APPLY_COVER_BUNDLE_INCOMPLETE:"]
    if missing:
        parts.append(f"missing={missing}")
    if bad:
        parts.append(f"bad_dimensions={bad}")
    if not audit.get("manifest_present"):
        parts.append("cover_dimensions.json missing")
    elif not audit.get("manifest_matches_ihdr"):
        parts.append("manifest does not match on-disk IHDR")
    parts.append("Use MCP visual_cover_asset normalize_registry then re-audit.")
    return " ".join(parts)
