"""Секция схемы ответа общая для агентов."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from agents.response_schema import (
    SCHEMA_SECTION_MARKER,
    FINAL_JSON_OUTPUT_MARKER,
    final_json_output_contract,
    response_schema_section,
)


class _Sample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    kind: str = "A"


def test_response_schema_section_lists_rules_and_keys():
    text = response_schema_section(_Sample)
    assert text.startswith(SCHEMA_SECTION_MARKER)
    assert "Любой другой ключ" in text
    assert "`ok` (required" in text
    assert '"properties"' in text
    assert '"title"' not in text
    assert "_Sample" not in text


def test_shared_schema_no_longer_invites_markdown_fences() -> None:
    text = response_schema_section(_Sample)
    assert "без Markdown-обёртки" in text
    assert "Допустим один markdown-блок" not in text
    assert "Первый символ ответа — {" in text


def test_tail_contract_is_role_agnostic_and_fail_closed() -> None:
    tail = final_json_output_contract()
    assert tail.startswith(FINAL_JSON_OUTPUT_MARKER)
    assert "ровно один" in tail
    assert "Первый символ финального ответа — {" in tail
    assert "последний — }" in tail
    assert "не заявляй PASS без evidence" in tail
    assert "Не выдумывай данные" in tail
