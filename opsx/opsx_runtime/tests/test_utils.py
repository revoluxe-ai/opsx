"""
Тесты для `agents/utils/utils.py` и `agents/utils/response.py`.

`get_schema_json` (см. ``agents.utils.utils``) давно отдаёт **markdown-секцию промпта**
``response_schema_section`` (правила + fenced JSON Schema), а не голый
``json.dumps(model_json_schema())``. Два legacy-теста ниже всё ещё ожидают
старый контракт — помечены skip до переписывания под prompt-секцию или
отдельную функцию «чистая схема».

Модуль `agents` лежит в `opsx/opsx_runtime`, поэтому путь добавляется явно,
чтобы тест работал независимо от настроек `pythonpath` в pyproject.toml.
"""

import json
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel

_RUNTIME_ROOT = Path(__file__).resolve().parents[1]
if str(_RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(_RUNTIME_ROOT))

from agents.utils.response import parse_agent_json  # noqa: E402
from agents.utils.utils import get_schema_json  # noqa: E402
from agents.state import BugBotAnswer  # noqa: E402


class _Answer(BaseModel):
    """Тестовая модель ответа агента."""

    verdict: str
    score: int
    comment: str | None = None


def test_get_schema_json_returns_str() -> None:
    """Функция возвращает строку."""
    result = get_schema_json(_Answer)

    assert isinstance(result, str)


# УСТАРЕВШИЙ КОНТРАКТ: ожидался голый JSON Schema; фактически — prompt markdown.
@pytest.mark.skip(
    reason="get_schema_json возвращает response_schema_section, не json.loads-able schema",
)
def test_get_schema_json_is_valid_json_matching_model_schema() -> None:
    """Строка - валидный JSON, совпадающий со схемой модели (legacy)."""
    result = get_schema_json(_Answer)

    parsed = json.loads(result)
    assert parsed == _Answer.model_json_schema()


def test_get_schema_json_keeps_unicode_and_indent() -> None:
    """ensure_ascii=False и indent=2: кириллица не экранируется, отступы есть."""
    class _Russian(BaseModel):
        """Модель с русским описанием."""

        комментарий: str = "проверка"

    result = get_schema_json(_Russian)

    assert "комментарий" in result
    assert "\\u" not in result
    assert "\n  " in result


# УСТАРЕВШИЙ КОНТРАКТ: см. test_get_schema_json_is_valid_json_matching_model_schema.
@pytest.mark.skip(
    reason="get_schema_json возвращает response_schema_section, не json.loads-able schema",
)
def test_get_schema_json_string_parses_back_into_model() -> None:
    """Строка парсится обратно (legacy; см. skip reason)."""
    schema_str = get_schema_json(_Answer)

    schema = json.loads(schema_str)
    assert set(schema["required"]) == {"verdict", "score"}

    payload = json.dumps({"verdict": "ok", "score": 5, "comment": "норм"}, ensure_ascii=False)
    model = _Answer.model_validate_json(payload)

    assert model == _Answer(verdict="ok", score=5, comment="норм")
    assert schema["properties"].keys() == _Answer.model_json_schema()["properties"].keys()


def test_parse_agent_json_accepts_bare_and_fenced() -> None:
    """parse_agent_json: голый JSON и один fence."""
    bare = parse_agent_json('{"verdict": "ok", "score": 1}', _Answer)
    assert bare == _Answer(verdict="ok", score=1)
    fenced = parse_agent_json(
        "```json\n" '{"verdict": "ok", "score": 2}' "\n```",
        _Answer,
    )
    assert fenced == _Answer(verdict="ok", score=2)


def test_parse_agent_json_validates_model() -> None:
    """parse_agent_json проваливает текст через переданную модель."""
    model = parse_agent_json('{"verdict": "ok", "score": 3}', _Answer)
    assert model == _Answer(verdict="ok", score=3)


def test_parse_agent_json_accepts_prose_before_fence() -> None:
    """Prose перед fence извлекается через последний завершённый блок."""
    model = parse_agent_json(
        "Please see:\n```json\n"
        '{"verdict": "ok", "score": 1}\n```',
        _Answer,
    )
    assert model == _Answer(verdict="ok", score=1)
