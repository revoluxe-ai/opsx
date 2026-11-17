"""Что страница одного запуска считает ошибкой.

Пока супервизор работает, сайт видит тот же ``RUNNING`` и общий лог.
Красная ошибка — только падение супервизора или его таймаут.
"""

from __future__ import annotations

from typing import Any, Mapping


def keep_site_running(record: Mapping[str, Any]) -> dict[str, Any]:
    """Снять с записи попытки поля, из-за которых страница закрывает запуск."""
    updated = dict(record)
    updated["status"] = "RUNNING"
    updated.pop("error", None)
    updated["site_error"] = False
    return updated


def site_shows_error(record: Mapping[str, Any]) -> bool:
    """True, если странице нужно показать красную ошибку.

    Сработавший супервизор и обычный недосданный артефакт сюда не входят:
    страница не разбирает его шаги. Ошибка — таймаут бюджета или падение
    самого супервизора.
    """
    stop = str(record.get("supervisor_stop") or "")
    if stop in {"SUPERVISOR_TIMEOUT", "EXCEPTION"}:
        return True
    if stop:
        return False
    if record.get("site_error") is True:
        return True
    return str(record.get("workflow_status") or "") == "SUPERVISOR_TIMEOUT"
