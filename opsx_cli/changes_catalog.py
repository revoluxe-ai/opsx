"""Каталог OpenSpec change-id для локальной панели workflow."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from pathlib import Path

_CHANGE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SKIP_DIRS = frozenset({"archive", "_template"})


def _preview_line(change_dir: Path) -> str:
    """Краткий preview из proposal/design/owner-contract (первая осмысленная строка)."""
    for name in ("proposal.md", "design.md", "owner-contract.md"):
        path = change_dir / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line == "---":
                continue
            if line.startswith("#"):
                return line.lstrip("#").strip()[:240]
            return line[:240]
    return ""


def _updated_timestamp(change_dir: Path) -> float:
    """Самое позднее mtime файлов change: правка внутри каталога, не только самой папки."""
    latest = change_dir.stat().st_mtime
    for path in change_dir.rglob("*"):
        if path.is_file():
            latest = max(latest, path.stat().st_mtime)
    return latest


def list_openspec_changes(project_root: Path) -> list[dict[str, str]]:
    """Change-id из ``openspec/changes``, сначала самый свежий по дате файлов."""
    root = project_root / "openspec" / "changes"
    if not root.is_dir():
        return []
    items: list[tuple[float, dict[str, str]]] = []
    for path in root.iterdir():
        if not path.is_dir() or path.name in _SKIP_DIRS:
            continue
        if not _CHANGE_ID.fullmatch(path.name):
            continue
        updated = _updated_timestamp(path)
        items.append((updated, {
            "id": path.name,
            "preview": _preview_line(path),
            "updated_at": datetime.fromtimestamp(updated, timezone.utc).isoformat(),
        }))
    items.sort(key=lambda item: item[0], reverse=True)
    return [item for _, item in items]