"""Детерминированная идентичность control-plane кода Temporal review/repair.

Отдельно от product candidate HEAD: фиксирует набор модулей, которые владеют
парсером hunt, workflow loop, admission и terminal mapping.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Bounded manifest — только модули, влияющие на PBH loop / parser / terminal policy.
_CONTROL_PLANE_MODULES: tuple[str, ...] = (
    "parsers.py",
    "workflow.py",
    "loop_engine.py",
    "activities.py",
    "models.py",
    "recovery_contract.py",
    "commit_materialization.py",
    "review_repair_terminal_policy.py",
    "pbh_admission_binding.py",
    "control_plane_code_identity.py",
    "repair_evidence_identity.py",
    "lanes.py",
    "config.py",
)

SCHEMA_VERSION = "temporal-control-plane-code-identity-v1"


def control_plane_module_paths() -> tuple[Path, ...]:
    """Абсолютные пути модулей manifest (для тестов и диагностики)."""
    return tuple(HERE / name for name in _CONTROL_PLANE_MODULES)


def compute_control_plane_code_identity() -> str:
    """SHA-256 (hex) содержимого manifest-модулей в стабильном порядке."""
    digest = hashlib.sha256()
    for name in _CONTROL_PLANE_MODULES:
        path = HERE / name
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\n")
    return digest.hexdigest()


def build_control_plane_identity_record() -> dict[str, str | list[str]]:
    """Запись для worker-state и admission checks."""
    return {
        "schema_version": SCHEMA_VERSION,
        "control_plane_code_identity": compute_control_plane_code_identity(),
        "modules": list(_CONTROL_PLANE_MODULES),
    }


def identity_record_json() -> str:
    """Стабильная JSON-строка identity record (для логов)."""
    return json.dumps(build_control_plane_identity_record(), ensure_ascii=False, sort_keys=True)
