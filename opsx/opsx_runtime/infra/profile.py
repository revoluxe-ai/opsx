"""Схема профиля disposable-среды. Файл профиля — единственное описание для процесса.

Продуктовые тесты этот модуль не правит и не запускает.
"""

from __future__ import annotations

from opsx.project_context import resource_path

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class ServiceProfile(BaseModel):
    """Один disposable-сервис: порт, identity, команда подъёма."""

    name: str
    image: str
    host_port: int
    database: str
    identity_comment: str
    compose_file: str
    compose_project: str


class RunPolicy(BaseModel):
    """Как процесс гоняет infra-tier. Пока serial, пока нет квалификации parallel."""

    workers: int = 1
    parallel_qualified: bool = False
    maxfail: int = 1
    circuit_breaker_threshold: int = 3


class InfraProfile(BaseModel):
    """Декларативный профиль. Секреты здесь не хранятся, только имена переменных."""

    services: list[ServiceProfile]
    env_var_names: list[str] = Field(default_factory=list)
    active_expr: str = "not requires_postgres"
    infra_expr: str = "requires_postgres"
    full_regression_triggers: list[str] = Field(default_factory=list)
    run: RunPolicy = Field(default_factory=RunPolicy)


def load_profile(path: Path) -> InfraProfile:
    """Прочитать YAML профиля. Битый файл — ошибка, не пустой профиль."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"infra profile must be a mapping: {path}")
    return InfraProfile.model_validate(raw)


def default_profile_path(repo_root: Path) -> Path:
    """Канонический путь профиля в репозитории."""
    return resource_path(repo_root, "opsx", "project", "infra", "infra-profile.yaml")
