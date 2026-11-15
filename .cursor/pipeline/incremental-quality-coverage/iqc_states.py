"""Состояния и коды IQC V1."""

from __future__ import annotations

from enum import Enum


class FileCoverageState(str, Enum):
    """Состояние production `.py` в политике incremental strict."""

    LEGACY_UNCOVERED = "LEGACY_UNCOVERED"
    STRICT_COVERED = "STRICT_COVERED"
    COVERAGE_EXPANSION = "COVERAGE_EXPANSION"


class ManifestActivation(str, Enum):
    """Статус активации manifest на product branch."""

    PROPOSED = "PROPOSED"
    ACTIVE = "ACTIVE"


class VerifiedScopeLayer(str, Enum):
    """Уровень доверия к VERIFIED_SCOPE (config ≠ evidence)."""

    CONFIGURED = "CONFIGURED"
    CANDIDATE_VERIFIED = "CANDIDATE_VERIFIED"
    EVIDENCE_VERIFIED = "EVIDENCE_VERIFIED"
