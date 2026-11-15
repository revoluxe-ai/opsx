"""Утилиты промпта: схема ответа через общий ``response_schema_section``."""

from pydantic import BaseModel

from agents.response_schema import response_schema_section


def get_schema_json(model: type[BaseModel]) -> str:
    """Секция промпта со схемой ответа (не голый JSON Schema)."""
    return response_schema_section(model)

