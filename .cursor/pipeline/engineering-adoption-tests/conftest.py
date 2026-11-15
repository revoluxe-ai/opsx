"""Pytest fixtures for engineering adoption acceptance tests."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
_PIPELINE_ADOPTION = REPO_ROOT / ".cursor/pipeline/engineering-adoption"
if str(_PIPELINE_ADOPTION) not in sys.path:
    sys.path.insert(0, str(_PIPELINE_ADOPTION))

from tools.openspec.opsx_engineering_adoption_gate import (  # noqa: E402
    CONTRACT_ID,
    CONTRACT_REL,
    LOCK_REL,
    QUARANTINE_REL,
    VERIFY_RECEIPT_REL,
    compute_config_fingerprint,
    compute_contract_content_hash,
    compute_verification_receipt_sha256,
)


def write_trusted_debt_board(root: Path) -> None:
    path = root / "opsx/runs/debt-board/open-items.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "opsx-debt-board-open-items-v1",
                "trust_provenance": "OPSX_DEBT_BOARD_INDEX",
                "items": [],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def write_minimal_projectization(root: Path) -> None:
    opsx = root / "opsx"
    opsx.mkdir(parents=True, exist_ok=True)
    _ = (opsx / "projectization.yaml").write_text(
        yaml.safe_dump(
            {"status": "COMPLETE", "normal_commands_enabled": True},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    write_trusted_debt_board(root)


def write_verified_adoption(
    root: Path,
    *,
    with_quarantine: bool = False,
    forged_hash: bool = False,
) -> None:
    """Минимальный валидный adoption bundle для gate-тестов."""

    contract_dir = root / "opsx/engineering-adoption"
    contract_dir.mkdir(parents=True, exist_ok=True)
    contract: dict = {
        "schema": "opsx.engineering_adoption.contract",
        "contract_id": CONTRACT_ID,
        "toolchain": {"typechecker": "pyright", "test_runner": "pytest"},
        "mandatory_checks": ["syntax:compileall"],
        "watched_config_paths": ["opsx/project/capabilities.yaml"],
        "quarantine_test_count": 1 if with_quarantine else 0,
        "verify_summary": {"full_suite_verification_status": "NOT_RUN"},
        "adopted_at": datetime.now(UTC).isoformat(),
    }
    content_hash = compute_contract_content_hash(contract)
    contract["content_hash"] = content_hash
    _ = (root / CONTRACT_REL).write_text(json.dumps(contract, indent=2), encoding="utf-8")

    receipt = {
        "schema": "opsx.engineering_adoption.verification_receipt",
        "contract_content_hash": compute_contract_content_hash(
            json.loads((root / CONTRACT_REL).read_text(encoding="utf-8"))
        ),
        "config_fingerprint": compute_config_fingerprint(root, contract),
        "verified_at": datetime.now(UTC).isoformat(),
        "verify_ok": True,
    }
    _ = (root / VERIFY_RECEIPT_REL).write_text(json.dumps(receipt, indent=2), encoding="utf-8")

    if with_quarantine:
        expires = (datetime.now(UTC) + timedelta(days=30)).isoformat()
        quarantine = {
            "schema": "opsx.engineering_adoption.quarantine",
            "records": [
                {
                    "test_node_id": "tests/unit/test_example.py::test_slow",
                    "reason": "hang",
                    "evidence": "timeout 30s",
                    "risk": "low",
                    "owner_authorization_ref": "owner-message:q-1",
                    "expires_at": expires,
                    "reactivation_condition": "fix env",
                    "counts_as_pass": False,
                }
            ],
        }
        _ = (root / QUARANTINE_REL).write_text(json.dumps(quarantine, indent=2), encoding="utf-8")

    lock_hash = content_hash
    if forged_hash:
        lock_hash = "f" * 64
    lock = {
        "status": "ADOPTED_WITH_QUARANTINE" if with_quarantine else "ADOPTED",
        "contract_id": CONTRACT_ID,
        "content_hash": lock_hash,
        "verification_receipt_sha256": compute_verification_receipt_sha256(receipt),
        "mutation_commands_enabled": True,
    }
    _ = (root / LOCK_REL).write_text(yaml.safe_dump(lock, sort_keys=False), encoding="utf-8")
