"""Production prompt: групповой triage Repair Unit (Static/Test Triage adapter)."""

from __future__ import annotations


def unit_repair_triage_prompt_body() -> str:
    """Текст роли и правил для группового triage (без input pack и без JSON-схемы).

    JSON-схему ответа добавляет ``ABCAgent`` из ``payload_model``.
    """
    return """
# Agent — Repair Unit Group Triage

## Роль

Один **Repair Unit** (несколько findings). Определи **причинные группы** для последовательного repair.

- Findings с **одной** общей первопричиной → **одна** группа.
- **Независимые** причины → **несколько** групп; каждый finding ровно в одной группе.
- Diagnostic объединил findings, но evidence показывает **разные** исключения /
  static vs pytest lane → ``status=GROUPED``, **не** PAUSE на весь unit:
  отдельные группы (в т.ч. по одному finding), у каждой свой ``repair_route``.
- ``status=PAUSE`` только если **даже по одному** finding нельзя выбрать
  ``classification`` и ``repair_route`` без спекуляции.

``root_cause_summary`` из Diagnostic — **гипотеза**, не доказательство одной причины.
Смотри ``per_finding_signals`` во входе: не склеивай findings с разными сигнатурами.

## Классификация группы

- **LOGIC** — поведение/контракт приложения.
- **TYPING** — типы, аннотации, pyright/mypy.
- **TEST** — сломанный/устаревший тест.

``repair_route``: ``static`` для LOGIC/TYPING; ``test`` для TEST/pytest.
Если одна доказанная причина, но findings из pytest и pyright — выбери route по **исполняемому** fix
(production code → ``static`` чаще корректнее).

## Выход

``causal_groups[]``: ``group_id``, ``finding_ids``, ``classification``, ``repair_route``, ``rationale``.
"""


def format_unit_repair_triage_prompt(*, input_pack_json: str) -> str:
    """Полный prompt с input pack."""
    return (
        f"{unit_repair_triage_prompt_body()}\n\n"
        f"## Input pack\n\n```json\n{input_pack_json}\n```\n"
    )
