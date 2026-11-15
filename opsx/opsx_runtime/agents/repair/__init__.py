"""Публичные имена repair-исполнителей с отложенной загрузкой.

При импорте дочернего scenario_test_repair Python сначала выполняет этот
__init__. Раньше он заранее импортировал execute_role -> SDK-адаптер,
поэтому offline-тест требовал установленный runtime адаптера.

Теперь только объявляем доступные имена; реальные классы подгружаем
при явном обращении к ним. Это сохраняет прежний API, но не меняет
execution authority, модель или инструменты живого агента.
"""
from __future__ import annotations

from importlib import import_module

# Ключ — прежний публичный символ пакета, значение — модуль-источник.
# Например, запрос RepairNodes подгрузит nodes_repair, но импорт
# agents.repair.scenario_test_repair сам по себе ничего не подгружает.
_EXPORT_MODULES = {
    "HopMeta": ".execute_role",
    "RoleExecution": ".execute_role",
    "make_hop": ".execute_role",
    "run_role": ".execute_role",
    "FixBugAgent": ".fix_bug",
    "GreenVerifierAgent": ".green_verifier",
    "InvariantCaptureAgent": ".invariant_capture",
    "HuntNodes": ".nodes_hunt",
    "RepairNodes": ".nodes_repair",
}

__all__ = list(_EXPORT_MODULES)


def __getattr__(name: str):
    # Python вызывает это только при обращении к пока не загруженному имени
    # пакета. Дочерние модули и константы без обращения не активируют SDK.
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    # Относительный импорт разрешён лишь для известных имён из таблицы.
    # Ошибки в зависимостях явно запрошенного live-агента не скрываются.
    return getattr(import_module(module_name, __name__), name)
