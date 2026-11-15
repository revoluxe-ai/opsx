"""Монтирование общих контрактов в промпты групп агентов.

Фрагменты лежат в ``opsx/contract_prompts``. Один манифест задаёт, какой
группе какой файл попадает в промпт. Отсутствующий фрагмент — ошибка, не пустая строка.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

import yaml

_CACHE: dict[str, tuple[str, str]] = {}


class ContractPromptMissingError(RuntimeError):
    """В манифесте назван фрагмент или группа, которых нет на диске."""


def contract_root() -> Path:
    """Каталог ``opsx/contract_prompts`` относительно этого модуля."""
    return Path(__file__).resolve().parents[3] / "contract_prompts"


def fragment_marker(name: str) -> str:
    """Маркер уже вставленного фрагмента. Повторная вставка его не дублирует."""
    return f"<!-- contract:{name} -->"


@lru_cache(maxsize=1)
def load_manifest() -> dict[str, list[str]]:
    """Имена фрагментов по группе. Кэш сбрасывается через ``load_manifest.cache_clear``."""
    path = contract_root() / "manifest.yaml"
    if not path.is_file():
        raise ContractPromptMissingError(f"manifest missing: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    groups = raw.get("groups") if isinstance(raw, dict) else None
    if not isinstance(groups, dict):
        raise ContractPromptMissingError("manifest has no groups")
    out: dict[str, list[str]] = {}
    for key, names in groups.items():
        if not isinstance(names, list) or not all(isinstance(item, str) for item in names):
            raise ContractPromptMissingError(f"group {key} is not a list of names")
        out[str(key)] = list(names)
    return out


def fragment_text(name: str) -> str:
    """Текст фрагмента с маркером. Кэш инвалидируется, когда меняется digest файла."""
    path = contract_root() / f"{name}.md"
    if not path.is_file():
        raise ContractPromptMissingError(name)
    raw = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    cached = _CACHE.get(name)
    if cached is not None and cached[0] == digest:
        return cached[1]
    body = f"{fragment_marker(name)}\n{raw.strip()}\n"
    _CACHE[name] = (digest, body)
    return body


def group_names(group: str, *, extra: tuple[str, ...] = ()) -> list[str]:
    """Имена фрагментов группы плюс явные extra. Неизвестная группа — ошибка."""
    groups = load_manifest()
    if group not in groups:
        raise ContractPromptMissingError(group)
    names = list(groups[group])
    for item in extra:
        if item not in names:
            names.append(item)
    return names


def mount(group: str, *, extra: tuple[str, ...] = ()) -> str:
    """Склеить фрагменты группы в стабильном порядке."""
    return "\n".join(fragment_text(name) for name in group_names(group, extra=extra))


def group_for_agent(agent_key: str | None) -> str:
    """Группа по имени агента. Неизвестное имя получает общий набор."""
    key = (agent_key or "").lower()
    if any(part in key for part in ("repair", "fix_bug", "hunter", "green_verifier")):
        return "repair"
    if any(part in key for part in ("plan", "scenario_coverage_plan")):
        return "planning"
    if any(
        part in key
        for part in ("verify", "bugbot", "security", "infra", "gate", "triage", "diagnostic")
    ):
        return "verify"
    if any(part in key for part in ("apply", "implement", "scaffold", "scenario_test_author")):
        return "implementation"
    return "common"


def prepend_contracts(
    prompt: str,
    *,
    group: str | None = None,
    agent_key: str | None = None,
    extra: tuple[str, ...] = (),
) -> str:
    """Поставить недостающие фрагменты в начало промпта. Уже вставленные не дублируются."""
    resolved = group or group_for_agent(agent_key)
    chunks: list[str] = []
    for name in group_names(resolved, extra=extra):
        if fragment_marker(name) not in prompt:
            chunks.append(fragment_text(name))
    if not chunks:
        return prompt
    return "\n".join(chunks) + "\n" + prompt
