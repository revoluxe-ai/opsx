"""Fail-closed engineering-adoption gate for OPSX mutation-capable commands.

Проверяет наличие верифицированного Adoption Contract (не маркер ADOPTED
в isolation). Bootstrap/read-only команды перечислены явно.
"""

from __future__ import annotations

from opsx.project_context import resource_path

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

GATE_IMPLEMENTATION_ID = "stroy-opsx-engineering-adoption-gate-v1.0.0"
CONTRACT_ID = "OPSX-PROJECT-ENGINEERING-ADOPTION-V1"

LOCK_REL = Path("opsx/engineering-adoption.yaml")
CONTRACT_DIR_REL = Path("opsx/engineering-adoption")
CONTRACT_REL = CONTRACT_DIR_REL / "adoption-contract.json"
VERIFY_RECEIPT_REL = CONTRACT_DIR_REL / "verification-receipt.json"
QUARANTINE_REL = CONTRACT_DIR_REL / "quarantined-tests.json"

ADOPTED_STATUSES = frozenset({"ADOPTED", "ADOPTED_WITH_QUARANTINE"})

# Команды, разрешённые без engineering adoption (bootstrap / read-only / recovery).
BOOTSTRAP_COMMAND_ALLOWLIST: frozenset[str] = frozenset(
    {
        "opsx-adopt",
        "opsx-projectize",
        "opsx-meta-bootstrap",
        "opsx-execution-preflight",
        "opsx-doctor",
        "opsx-source-resolution",
        "opsx-resolve-conflict",
        "opsx-meta-discover",
        "opsx-meta-consult",
    }
)

# Поверхности конфигурации: изменение после VERIFY делает evidence stale.
WATCHED_CONFIG_PATHS: tuple[str, ...] = (
    "pyrightconfig.json",
    "opsx/project/capabilities.yaml",
    "opsx/engineering-adoption/pyright-policy.json",
)


@dataclass(frozen=True)
class AdoptionGateResult:
    """Итог проверки adoption gate."""

    ok: bool
    reason: str
    detail: str = ""
    next_action: str = "/opsx-adopt"

    def as_blocked_dict(self) -> dict[str, str]:
        return {
            "STATUS": "FAIL_CLOSED",
            "REASON": self.reason,
            "NEXT_ACTION": self.next_action,
            "DETAIL": self.detail,
        }


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_json(obj: Any) -> str:
    return _sha256_bytes(
        json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    )


def read_adoption_lock(root: Path) -> dict[str, Any]:
    """Прочитать ``opsx/engineering-adoption.yaml`` или пустой dict."""

    path = resource_path(root, LOCK_REL)
    if not path.is_file():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def canonical_contract_body(contract: dict[str, Any]) -> dict[str, Any]:
    """Тело контракта без полей, не входящих в content hash."""

    omit = {"content_hash", "verification_receipt_sha256"}
    return {k: v for k, v in contract.items() if k not in omit}


def compute_contract_content_hash(contract: dict[str, Any]) -> str:
    return _sha256_json(canonical_contract_body(contract))


def compute_verification_receipt_sha256(receipt: dict[str, Any]) -> str:
    """SHA-256 канонического JSON receipt (binding в lock file)."""

    return _sha256_json(receipt)


def compute_config_fingerprint(root: Path, contract: dict[str, Any]) -> str:
    """Хеш watched config paths, зафиксированных в контракте."""

    paths = contract.get("watched_config_paths")
    if not isinstance(paths, list):
        paths = list(WATCHED_CONFIG_PATHS)
    entries: list[dict[str, str]] = []
    for rel in sorted({str(p) for p in paths}):
        abs_path = resource_path(root, rel)
        if abs_path.is_file():
            entries.append({"path": rel, "sha256": _sha256_bytes(abs_path.read_bytes())})
        else:
            entries.append({"path": rel, "sha256": "MISSING"})
    return _sha256_json(entries)


def _parse_expiry(iso: str) -> datetime | None:
    if not iso:
        return None
    try:
        normalized = iso.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def validate_quarantine_record(record: dict[str, Any], *, now: datetime) -> str | None:
    """Вернуть код ошибки или None если запись допустима."""

    required = (
        "test_node_id",
        "reason",
        "owner_authorization_ref",
        "evidence",
        "expires_at",
        "risk",
        "reactivation_condition",
    )
    for key in required:
        if not record.get(key):
            return "QUARANTINE_RECORD_INCOMPLETE"
    expiry = _parse_expiry(str(record.get("expires_at", "")))
    if expiry is None:
        return "QUARANTINE_EXPIRY_INVALID"
    if expiry < now:
        return "QUARANTINE_EXPIRED"
    if record.get("counts_as_pass") is True:
        return "QUARANTINE_COUNTS_AS_PASS_FORBIDDEN"
    return None


def validate_quarantine_file(root: Path) -> AdoptionGateResult | None:
    """Проверить quarantine file если lock требует quarantine."""

    path = resource_path(root, QUARANTINE_REL)
    if not path.is_file():
        return AdoptionGateResult(
            ok=False,
            reason="QUARANTINE_MANIFEST_MISSING",
            detail=str(QUARANTINE_REL),
        )
    data = _read_json(path)
    if data is None:
        return AdoptionGateResult(ok=False, reason="QUARANTINE_MANIFEST_INVALID")
    records = data.get("records")
    if not isinstance(records, list):
        return AdoptionGateResult(ok=False, reason="QUARANTINE_MANIFEST_INVALID")
    now = datetime.now(UTC)
    for record in records:
        if not isinstance(record, dict):
            return AdoptionGateResult(ok=False, reason="QUARANTINE_RECORD_INVALID")
        err = validate_quarantine_record(record, now=now)
        if err:
            return AdoptionGateResult(ok=False, reason=err, detail=str(record.get("test_node_id")))
    return None


def validate_verified_adoption(root: Path) -> AdoptionGateResult:
    """Полная проверка adoption lock + contract + receipt (anti-forgery)."""

    lock = read_adoption_lock(root)
    if not lock:
        return AdoptionGateResult(
            ok=False,
            reason="OPSX_ADOPTION_REQUIRED",
            detail="missing engineering-adoption.yaml",
        )

    status = str(lock.get("status", ""))
    if status not in ADOPTED_STATUSES:
        return AdoptionGateResult(
            ok=False,
            reason="OPSX_ADOPTION_REQUIRED",
            detail=f"status={status!r}",
        )

    if lock.get("contract_id") != CONTRACT_ID:
        return AdoptionGateResult(
            ok=False,
            reason="OPSX_ADOPTION_REQUIRED",
            detail="contract_id mismatch",
        )

    contract_path = resource_path(root, CONTRACT_REL)
    contract = _read_json(contract_path)
    if contract is None:
        return AdoptionGateResult(
            ok=False,
            reason="OPSX_ADOPTION_REQUIRED",
            detail="adoption-contract.json missing or invalid",
        )

    expected_hash = str(lock.get("content_hash", ""))
    actual_hash = compute_contract_content_hash(contract)
    if not expected_hash or expected_hash != actual_hash:
        return AdoptionGateResult(
            ok=False,
            reason="OPSX_ADOPTION_REQUIRED",
            detail="content_hash mismatch (forged or stale lock)",
        )

    if contract.get("contract_id") != CONTRACT_ID:
        return AdoptionGateResult(ok=False, reason="OPSX_ADOPTION_REQUIRED", detail="contract marker")

    receipt_path = resource_path(root, VERIFY_RECEIPT_REL)
    receipt = _read_json(receipt_path)
    if receipt is None:
        return AdoptionGateResult(
            ok=False,
            reason="OPSX_ADOPTION_REQUIRED",
            detail="verification-receipt.json missing",
        )

    receipt_contract_hash = str(receipt.get("contract_content_hash", ""))
    if receipt_contract_hash != actual_hash:
        return AdoptionGateResult(
            ok=False,
            reason="OPSX_ADOPTION_REQUIRED",
            detail="verification receipt does not bind contract",
        )

    if str(lock.get("verification_receipt_sha256", "")) != compute_verification_receipt_sha256(
        receipt
    ):
        return AdoptionGateResult(
            ok=False,
            reason="OPSX_ADOPTION_REQUIRED",
            detail="lock receipt binding mismatch",
        )

    fingerprint = compute_config_fingerprint(root, contract)
    if str(receipt.get("config_fingerprint", "")) != fingerprint:
        return AdoptionGateResult(
            ok=False,
            reason="OPSX_ADOPTION_STALE",
            detail="configuration changed since last VERIFY",
        )

    if status == "ADOPTED_WITH_QUARANTINE":
        q_err = validate_quarantine_file(root)
        if q_err is not None:
            return q_err

    if lock.get("mutation_commands_enabled") is not True:
        return AdoptionGateResult(
            ok=False,
            reason="OPSX_ADOPTION_REQUIRED",
            detail="mutation_commands_enabled is not true",
        )

    return AdoptionGateResult(ok=True, reason="OK")


def engineering_adoption_allows_mutations(
    root: Path,
    *,
    command_id: str | None = None,
) -> AdoptionGateResult:
    """Gate для mutation-capable OPSX: projectization должна быть пройдена отдельно."""

    if command_id and command_id in BOOTSTRAP_COMMAND_ALLOWLIST:
        return AdoptionGateResult(ok=True, reason="BOOTSTRAP_ALLOWLIST")

    return validate_verified_adoption(root)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OPSX engineering adoption mutation gate.")
    parser.add_argument(
        "--check-mutation",
        action="store_true",
        help="Exit 0 when verified adoption allows mutations; else print FAIL_CLOSED JSON.",
    )
    parser.add_argument("--command-id", default=None, help="Optional OPSX command id for allowlist.")
    parser.add_argument("--root", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = (args.root or Path.cwd()).resolve()
    if not args.check_mutation:
        print("FAIL USAGE: pass --check-mutation", file=sys.stderr)
        return 2
    result = engineering_adoption_allows_mutations(root, command_id=args.command_id)
    if result.ok:
        print(json.dumps({"STATUS": "OK", "implementation": GATE_IMPLEMENTATION_ID}))
        return 0
    print(json.dumps(result.as_blocked_dict()))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
