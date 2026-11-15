"""Pydantic-модель ответа LLM для hop генерации текста git commit."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CommitMessageResult(BaseModel):
    """Структурированное сообщение коммита на русском языке."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    agent: Literal["commit_message"] = "commit_message"
    subject: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1)
