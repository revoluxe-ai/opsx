"""Runtime-чтение repair worker-контрактов для встройки в SDK prompt (модель не читает ``.cursor/**``)."""

from __future__ import annotations

from pathlib import Path

from agents.common_prompt_data.repair import BUG_TEAM_POLICY_PATH
from agents.repair.product_surface import (
    format_repair_product_surface_prompt,
    is_repair_product_hop,
)
from agents.repair.role_contracts import worker_contract_path

def _repo_join(repo: Path, rel_posix: str) -> Path:
    return repo.joinpath(*[p for p in rel_posix.split("/") if p])


def _read_authority_text(path: Path) -> str:
    """Прочитать контракт целиком для вставки в промпт."""
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def embed_repair_worker_authority(
    role: str,
    *,
    repository_path: str,
) -> str:
    """Полный worker contract + (для hunter) policy JSON для prompt."""
    repo = Path(repository_path).resolve()
    blocks: list[str] = []
    contract_rel = worker_contract_path(role)
    contract_text = _read_authority_text(_repo_join(repo, contract_rel))
    if contract_text:
        blocks.append(f"### Worker contract (embedded)\n{contract_text.strip()}")
    if role == "BOUNDED_HUNTER":
        policy_text = _read_authority_text(_repo_join(repo, BUG_TEAM_POLICY_PATH))
        if policy_text:
            blocks.append(f"### Bug team policy (embedded)\n{policy_text.strip()}")
    if is_repair_product_hop(role):
        blocks.append(format_repair_product_surface_prompt().strip())
    if not blocks:
        return (
            "### Worker authority\n"
            "Следуй ROLE-SPECIFIC TASK и INPUT CONTEXT JSON ниже; "
            "не открывай `.cursor/**`, `opsx/receipts/**`, `opsx/runs/**`.\n"
        )
    return "\n\n".join(blocks) + "\n"
