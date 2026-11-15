"""Выбор infra-тестов change. Чистые функции, без чтения продуктовых тестов с диска.

Списки node id передаёт вызывающий. Этот модуль не открывает ``tests/**`` и не пишет в них.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class InfraSelection:
    """Набор к прогону и статус. Пустой набор — явный PASS_NO_SCOPE."""

    status: str
    node_ids: tuple[str, ...]
    reasons: tuple[str, ...] = field(default_factory=tuple)
    scope: str = "involved"


def select_infra_nodes(
    *,
    trace_nodes: tuple[str, ...] = (),
    slice_nodes: tuple[str, ...] = (),
    import_closure_nodes: tuple[str, ...] = (),
    all_infra_nodes: tuple[str, ...] = (),
    full_triggers_hit: bool = False,
    force_full: bool = False,
    disabled_nodes: tuple[str, ...] = (),
) -> InfraSelection:
    """S1–S4: trace, slice, import closure, иначе full по триггеру.

    Disabled node id вычитаются. Они не исполняются и не делают набор пустым молча:
    причина ``disabled`` остаётся в reasons.
    """
    reasons: list[str] = []
    if force_full or full_triggers_hit:
        chosen = tuple(dict.fromkeys(all_infra_nodes))
        reasons.append("full" if force_full else "full_trigger")
        scope = "full"
    else:
        chosen = tuple(dict.fromkeys((*trace_nodes, *slice_nodes, *import_closure_nodes)))
        scope = "involved"
        if trace_nodes:
            reasons.append("s1_trace")
        if slice_nodes:
            reasons.append("s2_slice")
        if import_closure_nodes:
            reasons.append("s3_imports")
    blocked = set(disabled_nodes)
    if blocked:
        reasons.append("disabled")
    runnable = tuple(node for node in chosen if node not in blocked)
    if not runnable:
        return InfraSelection("PASS_NO_SCOPE", (), tuple(reasons), scope=scope)
    return InfraSelection("SELECTED", runnable, tuple(reasons), scope=scope)
