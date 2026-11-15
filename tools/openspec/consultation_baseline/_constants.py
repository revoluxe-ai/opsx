"""Константы pin и путей для consultation baseline registry."""

from __future__ import annotations

from pathlib import Path

# Ревизия Owner Contract / design D-BL-01
BASE_SHA: str = "f0c28f98c86cc90aad94d107f647dfe770416e49"

# Immutable source @ BASE_SHA (decision ux-v9-transition-registry-verification)
CONTRACT_GIT_PATH: str = (
    "src/stroy/channels/telegram/invest_yalta/data/ux_state_contract.json"
)

CHANGE_ID: str = "consultation-funnel-baseline"

EVIDENCE_DIR_REL: str = f"openspec/changes/{CHANGE_ID}/evidence"

JSONL_FILENAME: str = "ux-v9-transition-registry.jsonl"
MANIFEST_FILENAME: str = "registry.manifest.json"


def repo_root_from_here() -> Path:
    """Корень репозитория относительно расположения пакета tools/openspec."""

    return Path(__file__).resolve().parents[3]


def evidence_dir(root: Path | None = None) -> Path:
    """Каталог change-local evidence для consultation-funnel-baseline."""

    base = root if root is not None else repo_root_from_here()
    return base / EVIDENCE_DIR_REL
