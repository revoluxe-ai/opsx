"""Реестр отключённых проверок адоптируемого проекта.

Читают gate, infra-selection и verify. Запись без этого файла не легализует skip.
"""

from __future__ import annotations

from opsx.project_context import resource_path

from dataclasses import dataclass
from pathlib import Path

import yaml

DISABLED_REL = Path("opsx/project/adoption/disabled-checks.yaml")


class DisabledCheckRegistryError(RuntimeError):
    """Реестр отсутствует или не проходит схему."""


@dataclass(frozen=True)
class DisabledCheck:
    """Одна отключённая проверка до включения change-ом, который её задействует."""

    id: str
    kind: str
    selector: str
    reason: str
    evidence: str
    enable_when: str
    status: str


def registry_path(repo_root: Path) -> Path:
    """Путь реестра в корне репозитория."""
    return resource_path(repo_root, DISABLED_REL)


def load_disabled_checks(repo_root: Path) -> tuple[DisabledCheck, ...]:
    """Прочитать реестр. Пустой ``checks`` допустим. Битый файл — ошибка."""
    path = registry_path(repo_root)
    if not path.is_file():
        raise DisabledCheckRegistryError(f"missing disabled-check registry: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise DisabledCheckRegistryError("registry root must be a mapping")
    rows = raw.get("checks", [])
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        raise DisabledCheckRegistryError("checks must be a list")
    parsed: list[DisabledCheck] = []
    for row in rows:
        if not isinstance(row, dict):
            raise DisabledCheckRegistryError("check row must be a mapping")
        kind = str(row.get("kind") or "")
        status = str(row.get("status") or "")
        if kind not in {"test", "gate", "check"}:
            raise DisabledCheckRegistryError(f"bad kind: {kind!r}")
        if status not in {"disabled", "enabled"}:
            raise DisabledCheckRegistryError(f"bad status: {status!r}")
        parsed.append(
            DisabledCheck(
                id=str(row.get("id") or ""),
                kind=kind,
                selector=str(row.get("selector") or ""),
                reason=str(row.get("reason") or ""),
                evidence=str(row.get("evidence") or ""),
                enable_when=str(row.get("enable_when") or ""),
                status=status,
            )
        )
        if not parsed[-1].id or not parsed[-1].selector:
            raise DisabledCheckRegistryError("id and selector are required")
    return tuple(parsed)


def active_disabled(checks: tuple[DisabledCheck, ...]) -> tuple[DisabledCheck, ...]:
    """Только ещё отключённые записи."""
    return tuple(row for row in checks if row.status == "disabled")


def selector_matches(selector: str, node_id: str) -> bool:
    """Совпадение node id с точным selector или glob ``*`` на один сегмент пути."""
    if selector == node_id:
        return True
    if "*" not in selector:
        return False
    import fnmatch

    return fnmatch.fnmatch(node_id, selector)


def disabled_for_node(checks: tuple[DisabledCheck, ...], node_id: str) -> DisabledCheck | None:
    """Первая disabled-запись, чей selector покрывает node id."""
    for row in active_disabled(checks):
        if row.kind == "test" and selector_matches(row.selector, node_id):
            return row
    return None


def assert_scope_enables_disabled(
    checks: tuple[DisabledCheck, ...],
    node_ids: tuple[str, ...],
    *,
    change_id: str,
) -> None:
    """Сценарий change не может ссылаться на disabled-тест, не назвав этот change в enable_when."""
    for node_id in node_ids:
        row = disabled_for_node(checks, node_id)
        if row is None:
            continue
        if change_id and change_id == row.enable_when:
            continue
        raise DisabledCheckRegistryError(
            f"DISABLED_CHECK_IN_SCOPE: {node_id} disabled as {row.id}; enable_when={row.enable_when}"
        )
