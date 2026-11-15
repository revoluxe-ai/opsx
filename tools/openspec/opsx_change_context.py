"""Локальный default OpenSpec change id (.tmp) и явные токены в аргументах OPSX.

Связь: `/opsx-current-change`, `opsx-source-resolution.md` § CHANGE CONTEXT.
Единственный SSOT binding: `.tmp/change-config.toml`. Каталог `.tpm/` — только
supervisor-артефакты; misplaced `change-config.toml` там удаляется при `set`.
"""

from __future__ import annotations

from opsx.project_context import resource_path

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CHANGE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
CHANGE_CONFIG_REL = Path(".tmp/change-config.toml")
MISPLACED_CHANGE_CONFIG_REL = Path(".tpm/change-config.toml")
EXPLICIT_CHANGE_RE = re.compile(
    r"(?:^|\s)change(?:-id)?=([a-z0-9][a-z0-9-]*)(?:\s|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CurrentChangeBinding:
    """Валидная привязка current change из `.tmp/change-config.toml`."""

    change_id: str
    change_root: str
    schema: str
    revision_at_set: str | None
    set_at_utc: str | None


def _parse_toml_change_id(text: str) -> str | None:
    match = re.search(r'^\s*change_id\s*=\s*"([^"]+)"\s*$', text, re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip()


def _binding_from_file(repo: Path, path: Path) -> CurrentChangeBinding | None:
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    change_id = _parse_toml_change_id(raw)
    if not change_id or CHANGE_ID_PATTERN.fullmatch(change_id) is None:
        return None
    change_root = resource_path(repo, "openspec", "changes", change_id)
    if not change_root.is_dir():
        return None
    schema_m = re.search(r'^\s*schema\s*=\s*"([^"]+)"', raw, re.MULTILINE)
    rev_m = re.search(r'^\s*revision_at_set\s*=\s*"([^"]+)"', raw, re.MULTILINE)
    at_m = re.search(r'^\s*set_at_utc\s*=\s*"([^"]+)"', raw, re.MULTILINE)
    return CurrentChangeBinding(
        change_id=change_id,
        change_root=change_root.relative_to(repo).as_posix(),
        schema=schema_m.group(1) if schema_m else "opsx.current_change.v1",
        revision_at_set=rev_m.group(1) if rev_m else None,
        set_at_utc=at_m.group(1) if at_m else None,
    )


def _remove_misplaced_change_binding(repo_root: Path) -> None:
    """Удалить ошибочно созданный binding под `.tpm/` (не SSOT)."""
    path = resource_path(repo_root, MISPLACED_CHANGE_CONFIG_REL)
    if path.is_file():
        path.unlink()


def load_current_change(repo_root: Path) -> CurrentChangeBinding | None:
    """Прочитать binding из `.tmp/change-config.toml`, если каталог change существует."""
    repo = repo_root.resolve()
    return _binding_from_file(repo, (resource_path(repo, CHANGE_CONFIG_REL)).resolve())


def extract_explicit_change_id(arguments: str) -> str | None:
    """Извлечь change= / change-id= из строки аргументов slash-команды."""
    if not arguments.strip():
        return None
    match = EXPLICIT_CHANGE_RE.search(f" {arguments.strip()} ")
    if not match:
        return None
    change_id = match.group(1).strip()
    if CHANGE_ID_PATTERN.fullmatch(change_id) is None:
        return None
    return change_id


def resolve_change_id(
    repo_root: Path,
    arguments: str = "",
) -> tuple[str | None, str]:
    """Вернуть (change_id, source): explicit | tmp | explicit_invalid | none."""
    explicit = extract_explicit_change_id(arguments)
    if explicit:
        root = resource_path(repo_root, "openspec", "changes", explicit)
        if root.is_dir():
            return explicit, "explicit"
        return None, "explicit_invalid"
    repo = repo_root.resolve()
    primary = _binding_from_file(repo, (resource_path(repo, CHANGE_CONFIG_REL)).resolve())
    if primary is not None:
        return primary.change_id, "tmp"
    return None, "none"


def write_current_change_binding(
    repo_root: Path,
    change_id: str,
    *,
    revision_at_set: str | None = None,
    set_by: str = "/opsx-current-change",
) -> Path:
    """Записать `.tmp/change-config.toml` (вызывается из `/opsx-current-change set`)."""
    if CHANGE_ID_PATTERN.fullmatch(change_id) is None:
        raise ValueError("invalid_change_id")
    change_root = resource_path(repo_root, "openspec", "changes", change_id)
    if not change_root.is_dir():
        raise ValueError("missing_change_root")
    tmp_dir = resource_path(repo_root, ".tmp")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    path = resource_path(repo_root, CHANGE_CONFIG_REL)
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    lines = [
        'schema = "opsx.current_change.v1"',
        f'change_id = "{change_id}"',
        f'change_root = "openspec/changes/{change_id}"',
    ]
    if revision_at_set:
        lines.append(f'revision_at_set = "{revision_at_set}"')
    lines.append(f'set_at_utc = "{now}"')
    lines.append(f'set_by = "{set_by}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _remove_misplaced_change_binding(repo_root)
    return path


def clear_current_change_binding(repo_root: Path) -> None:
    """Удалить локальный binding (.tmp SSOT и misplaced `.tpm/change-config.toml`)."""
    for rel in (CHANGE_CONFIG_REL, MISPLACED_CHANGE_CONFIG_REL):
        path = resource_path(repo_root, rel)
        if path.is_file():
            path.unlink()


def binding_as_dict(binding: CurrentChangeBinding | None) -> dict[str, Any] | None:
    """JSON-serializable snapshot for CLI / tests."""
    if binding is None:
        return None
    return {
        "change_id": binding.change_id,
        "change_root": binding.change_root,
        "schema": binding.schema,
        "revision_at_set": binding.revision_at_set,
        "set_at_utc": binding.set_at_utc,
    }
