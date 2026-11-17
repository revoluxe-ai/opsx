"""Загрузка контрактов супервизора из ``contract_prompts/process_supervisor``."""

from __future__ import annotations

from pathlib import Path

_SUPERVISOR_CONTRACT = "SUPERVISOR.md"
_QUALIFIER_AGENT_CONTRACT = "QUALIFIER_AGENT.md"
_FIX_AGENT_CONTRACT = "FIX_AGENT.md"


def load_supervisor_orchestration_contract(core_root: Path) -> str:
    """Текст контракта orchestration для супервизора (канон — ``SUPERVISOR.md``)."""
    path = core_root / "opsx/contract_prompts/process_supervisor" / _SUPERVISOR_CONTRACT
    if not path.is_file():
        return f"(контракт не найден: {path.as_posix()})"
    return path.read_text(encoding="utf-8")


def load_process_supervisor_qualifier_contract(core_root: Path) -> str:
    """Текст контракта qualifier-hop (только классификация, без write)."""
    path = core_root / "opsx/contract_prompts/process_supervisor" / _QUALIFIER_AGENT_CONTRACT
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def load_process_supervisor_fix_contract(core_root: Path) -> str:
    """Текст контракта fix-hop ``PROCESS_SUPERVISOR_FIX``."""
    path = core_root / "opsx/contract_prompts/process_supervisor" / _FIX_AGENT_CONTRACT
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")