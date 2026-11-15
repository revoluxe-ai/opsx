"""Prompt hop генерации текста git commit."""

from __future__ import annotations

_ROLE = """
# Agent — Commit Message

## Роль

Ты формируешь **только** текст локального git commit по уже применённому fix.
**Не** меняй файлы, **не** запускай commit/push — верни JSON с ``subject`` и ``body``.

## Язык

- ``subject`` и ``body`` — **строго на русском**.
- ``subject``: одна строка, ≤72 символов, смысл изменения (можно префикс ``fix:``, ``feat:``).
- ``body``: 3–8 строк: что изменено, зачем, контекст finding (если есть).

## Вход

``fix_evidence`` (summary, files_changed, notes), опционально ``finding``,
``staged_diff_stat`` — stat проиндексированных изменений.
"""


def get_prompt(
    *,
    input_pack_json: str,
    commit_message_task: str | None = None,
) -> str:
    """Собрать prompt генерации commit message.

    JSON-схему ответа не вставляет: её добавляет ``ABCAgent``.
    """
    task_block = ""
    if commit_message_task and commit_message_task.strip():
        task_block = (
            "\n## Задача этого коммита\n\n"
            f"{commit_message_task.strip()}\n\n"
            "Сформируй subject/body в соответствии с этой задачей; "
            "общий шаблон fix evidence — контекст, не противоречащий задаче.\n"
        )
    return (
        f"{_ROLE.strip()}\n"
        f"{task_block}\n"
        f"## Input package (JSON)\n\n```json\n{input_pack_json}\n```\n"
    )
