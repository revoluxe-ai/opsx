"""Загрузка verify-hop authority из ``opsx/contract_prompts`` (без slash-команд)."""

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
def verify_impl_hop_authority() -> str:
    """Authority PHASE_IMPL verify из ``verify_impl_hop.md``."""
    try:
        return _body_from_fragment("verify_impl_hop")
    except ContractPromptMissingError as exc:
        raise ContractPromptMissingError(
            "verify_impl_hop fragment missing in opsx/contract_prompts",
        ) from exc


@lru_cache(maxsize=8)
def verify_archive_hop_authority() -> str:
    """Authority PHASE_ARCHIVE verify из ``verify_archive_hop.md``."""
    try:
        return _body_from_fragment("verify_archive_hop")
    except ContractPromptMissingError as exc:
        raise ContractPromptMissingError(
            "verify_archive_hop fragment missing in opsx/contract_prompts",
        ) from exc


@lru_cache(maxsize=8)
def verify_scenario_coverage_audit_authority() -> str:
    """Authority full scenario coverage audit из ``verify_scenario_coverage_audit.md``."""
    try:
        return _body_from_fragment("verify_scenario_coverage_audit")
    except ContractPromptMissingError as exc:
        raise ContractPromptMissingError(
            "verify_scenario_coverage_audit fragment missing in opsx/contract_prompts",
        ) from exc


__all__ = [
    "verify_archive_hop_authority",
    "verify_impl_hop_authority",
    "verify_scenario_coverage_audit_authority",
]