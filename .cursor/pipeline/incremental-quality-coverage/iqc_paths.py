"""Пути и константы IQC V1 (incremental quality coverage)."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PROPOSED_REL = Path("opsx/incremental-quality-coverage/manifest.proposed.yaml")
MANIFEST_ACTIVE_REL = Path("opsx/incremental-quality-coverage/manifest.yaml")
SCHEMA_REL = Path("opsx/schemas/incremental-quality-coverage-manifest-v1.schema.json")
EVIDENCE_CONTRACT = "INCREMENTAL_QUALITY_COVERAGE_REPORT"
CONTRACT_ID = "OPSX-INCREMENTAL-QUALITY-COVERAGE-V1"
