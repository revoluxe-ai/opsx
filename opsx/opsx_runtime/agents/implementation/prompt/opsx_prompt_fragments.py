"""Загрузка OPSX prompt-фрагментов для implementation/verify apply hops."""

from __future__ import annotations

from functools import lru_cache

from agents.common.contract_prompts import ContractPromptMissingError, fragment_text


def _body_from_fragment(name: str) -> str:
    """Вернуть текст фрагмента без HTML-маркера contract_prompts."""
    raw = fragment_text(name)
    lines = raw.splitlines()
    if lines and lines[0].startswith("<!-- contract:"):
        lines = lines[1:]
    return "\n".join(lines).strip()


@lru_cache(maxsize=8)
def verify_apply_hop_authority() -> str:
    """Authority verify-side apply hop из ``opsx/contract_prompts/verify_apply_hop.md``."""
    try:
        return _body_from_fragment("verify_apply_hop")
    except ContractPromptMissingError as exc:
        raise ContractPromptMissingError(
            "verify_apply_hop fragment missing in opsx/contract_prompts",
        ) from exc


@lru_cache(maxsize=8)
def scenario_test_repair_constraints() -> str:
    """Test-only repair constraints из ``scenario_test_repair_constraints.md``."""
    try:
        return _body_from_fragment("scenario_test_repair_constraints")
    except ContractPromptMissingError as exc:
        raise ContractPromptMissingError(
            "scenario_test_repair_constraints fragment missing in opsx/contract_prompts",
        ) from exc


__all__ = ["verify_apply_hop_authority", "scenario_test_repair_constraints"]