"""Input pack для ``test_harness_repair`` внутри ``pytest_execute``."""

from __future__ import annotations

from opsx.project_context import resource_path

from typing import Any

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from agents.common.prompt_embed import read_bounded_text
from agents.verify.pytest_run_health import CollectHealthResult


class TestHarnessRepairInputPack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository_path: str
    observation: str
    affected_modules: list[str] = Field(default_factory=list)
    collect_exit_code: int = 0
    collect_log_tail: str = ""
    embedded_modules: dict[str, str] = Field(default_factory=dict)


def build_harness_repair_input_pack(
    *,
    repository_path: str,
    item: dict[str, Any],
    state: dict[str, Any],
    collect: CollectHealthResult,
) -> TestHarnessRepairInputPack:
    """Собрать pack для prompt (без LLM-полей triage)."""
    _ = state
    modules = list(item.get("affected_modules") or [])
    repo = Path(repository_path)
    embedded: dict[str, str] = {}
    for rel in modules[:8]:
        text = read_bounded_text(resource_path(repo, rel.replace("\\", "/")), max_chars=12_000)
        if text:
            embedded[rel] = text
    for conf in ("tests/conftest.py", "tests/integration/conftest.py"):
        text = read_bounded_text(resource_path(repo, conf), max_chars=8_000)
        if text:
            embedded[conf] = text
    return TestHarnessRepairInputPack(
        repository_path=repository_path,
        observation=str(item.get("observation") or collect.log_tail[:2000]),
        affected_modules=modules,
        collect_exit_code=collect.exit_code,
        collect_log_tail=collect.log_tail[-6000:],
        embedded_modules=embedded,
    )
