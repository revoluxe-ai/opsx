# -*- coding: utf-8 -*-
"""Версионирование и флаги активации Bug History V3 (live off by default)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Literal

ContractVersion = Literal["legacy", "v3-forward", "v3", "EXEMPT"]


@dataclass(frozen=True)
class CausalHistorySettings:
    """Настройки causal history для одного case root."""

    history_contract_version: ContractVersion
    live_v3_append_enabled: bool
    history_integrity_closure_gate: bool
    legacy_prefix_unrecorded: bool

    @classmethod
    def default_legacy(cls) -> "CausalHistorySettings":
        return cls(
            history_contract_version="legacy",
            live_v3_append_enabled=False,
            history_integrity_closure_gate=False,
            legacy_prefix_unrecorded=True,
        )

    @classmethod
    def synthetic_v3(cls) -> "CausalHistorySettings":
        return cls(
            history_contract_version="v3",
            live_v3_append_enabled=True,
            history_integrity_closure_gate=False,
            legacy_prefix_unrecorded=False,
        )


def settings_path(case_root: Path) -> Path:
    return Path(case_root) / "results" / "causal" / "SETTINGS.json"


def load_settings(case_root: Path) -> CausalHistorySettings:
    """
    Загружает SETTINGS.json; отсутствие файла ⇒ legacy (append выключен).
    """
    path = settings_path(case_root)
    if not path.is_file():
        return CausalHistorySettings.default_legacy()
    data: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return CausalHistorySettings(
        history_contract_version=str(data.get("HISTORY_CONTRACT_VERSION", "legacy")),
        live_v3_append_enabled=bool(data.get("LIVE_V3_APPEND_ENABLED", False)),
        history_integrity_closure_gate=bool(data.get("HISTORY_INTEGRITY_CLOSURE_GATE", False)),
        legacy_prefix_unrecorded=bool(data.get("LEGACY_PREFIX_UNRECORDED", True)),
    )


def write_settings(case_root: Path, settings: CausalHistorySettings) -> Path:
    """Записывает SETTINGS (harness / synthetic cases only)."""
    path = settings_path(case_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "HISTORY_CONTRACT_VERSION": settings.history_contract_version,
                "LIVE_V3_APPEND_ENABLED": settings.live_v3_append_enabled,
                "HISTORY_INTEGRITY_CLOSURE_GATE": settings.history_integrity_closure_gate,
                "LEGACY_PREFIX_UNRECORDED": settings.legacy_prefix_unrecorded,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path
