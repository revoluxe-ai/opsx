"""Durable receipt helper for target-native OPSX intent alignment.

The helper computes the raw-request/restatement identity without writing during
preparation.  After an explicit Owner confirmation it atomically creates one
receipt below ``opsx/intents``.  It does not interpret product semantics,
write planning artifacts, or transfer receipts into an OpenSpec change.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn


HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_CONFIRMATIONS = {
    "confirm",
    "yes",
    "да",
    "подтверждаю",
    "согласовано",
    "restatement confirmed",
}
AMBIGUOUS_CONFIRMATIONS = {"go", "ok", "proceed"}
CONTRACT_ID = "OPSX-WB-INTENT-ALIGN-01"


class AlignmentError(ValueError):
    """Raised when an intent-alignment transition is invalid or stale."""


def _fail(message: str) -> NoReturn:
    raise AlignmentError(message)


def _required_text(value: str, field_name: str) -> str:
    if not value.strip():
        _fail(f"{field_name} must be non-empty")
    return value


def sha256_text(value: str) -> str:
    """Return the lowercase SHA256 digest of exact UTF-8 text."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def compute_identity(raw_request: str, system_restatement: str) -> dict[str, str]:
    """Compute the immutable hash identity for one visible restatement."""

    raw = _required_text(raw_request, "raw request")
    restatement = _required_text(system_restatement, "system restatement")
    raw_hash = sha256_text(raw)
    restatement_hash = sha256_text(restatement)
    intent_hash = sha256_text(f"{raw_hash}:{restatement_hash}")
    return {
        "RAW_REQUEST_SHA256": raw_hash,
        "SYSTEM_RESTATEMENT_SHA256": restatement_hash,
        "INTENT_HASH": intent_hash,
    }


def resolve_receipt_path(repo_root: Path, intent_hash: str) -> Path:
    """Resolve the sole receipt path and reject path-like or malformed hashes."""

    if HASH_PATTERN.fullmatch(intent_hash) is None:
        _fail("INTENT_HASH must be exactly 64 lowercase hexadecimal characters")
    intents_root = (repo_root.resolve() / "opsx" / "intents").resolve()
    receipt_path = (intents_root / f"{intent_hash}.md").resolve()
    if receipt_path.parent != intents_root:
        _fail("receipt path escapes opsx/intents")
    return receipt_path


def _payload_hash(
    *,
    identity: dict[str, str],
    change_id: str,
    system_restatement: str,
    owner_problem: str,
    owner_visible_behavior: str,
    invariants_scope: str,
    raw_request: str,
    confirmation_evidence: str,
) -> str:
    payload = {
        "identity": identity,
        "change_id": change_id,
        "system_restatement": system_restatement,
        "owner_problem": owner_problem,
        "owner_visible_behavior": owner_visible_behavior,
        "invariants_scope": invariants_scope,
        "raw_request": raw_request,
        "confirmation_evidence": confirmation_evidence,
    }
    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return sha256_text(serialized)


def _render_receipt(
    *,
    identity: dict[str, str],
    payload_hash: str,
    change_id: str,
    system_restatement: str,
    owner_problem: str,
    owner_visible_behavior: str,
    invariants_scope: str,
    raw_request: str,
    confirmation_evidence: str,
) -> str:
    confirmed_at = datetime.now(UTC).isoformat()
    return "\n".join(
        [
            "# Intent Alignment Receipt",
            "",
            "COMMAND: /opsx-intent-align",
            f"CHANGE_ID: {change_id}",
            "INTENT_STATUS: ALIGNED",
            f"INTENT_HASH: {identity['INTENT_HASH']}",
            f"RAW_REQUEST_SHA256: {identity['RAW_REQUEST_SHA256']}",
            "SYSTEM_RESTATEMENT_SHA256: "
            f"{identity['SYSTEM_RESTATEMENT_SHA256']}",
            f"ALIGNMENT_PAYLOAD_SHA256: {payload_hash}",
            f"CONFIRMED_AT: {confirmed_at}",
            "CONFIRMED_BY: /opsx-intent-align",
            f"CONTRACT_ID: {CONTRACT_ID}",
            "CLOSED_BY: /opsx-intent-align",
            "",
            "SYSTEM_RESTATEMENT_CONFIRMED: YES",
            "OWNER_CONFIRMATION: YES",
            "TECHNICAL_DECISIONS_MADE: NONE",
            "MATERIAL_AMBIGUITIES: []",
            "UNRESOLVED_HIGH_IMPACT_ASSUMPTIONS: []",
            "",
            "## System Restatement",
            system_restatement,
            "",
            "## Owner Problem & Why",
            owner_problem,
            "",
            "## Owner-Visible Behavior",
            owner_visible_behavior,
            "",
            "## Invariants & Scope Boundaries",
            invariants_scope,
            "",
            "## Provenance",
            f"- RAW_REQUEST: {raw_request}",
            f"- OWNER_CONFIRMATION_EVIDENCE: {confirmation_evidence}",
            "",
        ]
    )


def _read_header(body: str, field_name: str) -> str | None:
    match = re.search(
        rf"(?m)^{re.escape(field_name)}: (.+)$",
        body,
    )
    return match.group(1).strip() if match is not None else None


def _read_section(body: str, heading: str) -> str | None:
    pattern = rf"(?ms)^## {re.escape(heading)}\n(.*?)(?=^## |\Z)"
    match = re.search(pattern, body)
    return match.group(1).strip() if match is not None else None


def _validate_existing(
    receipt_path: Path,
    identity: dict[str, str],
    payload_hash: str,
    *,
    change_id: str,
    system_restatement: str,
    owner_problem: str,
    owner_visible_behavior: str,
    invariants_scope: str,
    raw_request: str,
    confirmation_evidence: str,
) -> None:
    body = receipt_path.read_text(encoding="utf-8")
    status_lines = re.findall(r"(?m)^INTENT_STATUS: (.+)$", body)
    if status_lines != ["ALIGNED"]:
        _fail("existing staging receipt is not exclusively ALIGNED")
    expected_headers = {
        "CHANGE_ID": change_id,
        "INTENT_HASH": identity["INTENT_HASH"],
        "RAW_REQUEST_SHA256": identity["RAW_REQUEST_SHA256"],
        "SYSTEM_RESTATEMENT_SHA256": identity["SYSTEM_RESTATEMENT_SHA256"],
        "ALIGNMENT_PAYLOAD_SHA256": payload_hash,
    }
    for field_name, expected in expected_headers.items():
        if _read_header(body, field_name) != expected:
            _fail(f"existing staging receipt conflicts on {field_name}")
    expected_sections = {
        "System Restatement": system_restatement,
        "Owner Problem & Why": owner_problem,
        "Owner-Visible Behavior": owner_visible_behavior,
        "Invariants & Scope Boundaries": invariants_scope,
        "Provenance": (
            f"- RAW_REQUEST: {raw_request}\n"
            f"- OWNER_CONFIRMATION_EVIDENCE: {confirmation_evidence}"
        ),
    }
    for heading, expected in expected_sections.items():
        if _read_section(body, heading) != expected.strip():
            _fail(f"existing staging receipt conflicts in {heading}")


def confirm_alignment(
    *,
    repo_root: Path,
    raw_request: str,
    system_restatement: str,
    expected_intent_hash: str,
    prior_challenge_hash: str,
    confirmation: str,
    confirmation_evidence: str,
    change_id: str,
    owner_problem: str,
    owner_visible_behavior: str,
    invariants_scope: str,
) -> tuple[Path, bool]:
    """Validate confirmation and atomically create an idempotent receipt.

    Returns:
        The exact receipt path and whether this call created it.

    Raises:
        AlignmentError: If confirmation, hashes, path, or existing state is
            invalid, ambiguous, conflicting, or stale.
    """

    normalized_confirmation = confirmation.strip().casefold()
    if normalized_confirmation in AMBIGUOUS_CONFIRMATIONS:
        _fail("AMBIGUOUS_CONFIRMATION_TOKEN")
    if normalized_confirmation not in ALLOWED_CONFIRMATIONS:
        _fail("OWNER_CONFIRMATION_REQUIRED")
    evidence = _required_text(confirmation_evidence, "confirmation evidence")
    if evidence.strip().casefold() != normalized_confirmation:
        _fail("confirmation evidence must exactly match the explicit token")

    identity = compute_identity(raw_request, system_restatement)
    computed_hash = identity["INTENT_HASH"]
    if expected_intent_hash != computed_hash:
        _fail("INTENT_ALIGNMENT_STALE: expected hash does not match current text")
    if prior_challenge_hash != computed_hash:
        _fail("INTENT_ALIGNMENT_STALE: prior challenge does not match current text")

    change = change_id.strip() or "unassigned"
    problem = _required_text(owner_problem, "owner problem")
    behavior = _required_text(owner_visible_behavior, "owner-visible behavior")
    boundaries = _required_text(invariants_scope, "invariants and scope")
    payload_hash = _payload_hash(
        identity=identity,
        change_id=change,
        system_restatement=system_restatement,
        owner_problem=problem,
        owner_visible_behavior=behavior,
        invariants_scope=boundaries,
        raw_request=raw_request,
        confirmation_evidence=evidence,
    )
    receipt_path = resolve_receipt_path(repo_root, computed_hash)
    if receipt_path.exists():
        _validate_existing(
            receipt_path,
            identity,
            payload_hash,
            change_id=change,
            system_restatement=system_restatement,
            owner_problem=problem,
            owner_visible_behavior=behavior,
            invariants_scope=boundaries,
            raw_request=raw_request,
            confirmation_evidence=evidence,
        )
        return receipt_path, False

    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    body = _render_receipt(
        identity=identity,
        payload_hash=payload_hash,
        change_id=change,
        system_restatement=system_restatement,
        owner_problem=problem,
        owner_visible_behavior=behavior,
        invariants_scope=boundaries,
        raw_request=raw_request,
        confirmation_evidence=evidence,
    )
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{computed_hash}.", suffix=".tmp", dir=receipt_path.parent
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            _ = stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary_path, receipt_path)
        except FileExistsError:
            _validate_existing(
                receipt_path,
                identity,
                payload_hash,
                change_id=change,
                system_restatement=system_restatement,
                owner_problem=problem,
                owner_visible_behavior=behavior,
                invariants_scope=boundaries,
                raw_request=raw_request,
                confirmation_evidence=evidence,
            )
            return receipt_path, False
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return receipt_path, True


def build_parser() -> argparse.ArgumentParser:
    """Build the prepare/confirm command-line parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("action", choices=("prepare", "confirm"))
    _ = parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    _ = parser.add_argument("--raw-request", required=True)
    _ = parser.add_argument("--system-restatement", required=True)
    _ = parser.add_argument("--expected-intent-hash")
    _ = parser.add_argument("--prior-challenge-hash")
    _ = parser.add_argument("--confirmation")
    _ = parser.add_argument("--confirmation-evidence")
    _ = parser.add_argument("--change-id", default="unassigned")
    _ = parser.add_argument("--owner-problem")
    _ = parser.add_argument("--owner-visible-behavior")
    _ = parser.add_argument("--invariants-scope")
    return parser


def _required_option(value: str | None, option_name: str) -> str:
    if value is None:
        _fail(f"{option_name} is required for confirm")
    return value


def main() -> int:
    """Compute a challenge identity or persist a confirmed alignment receipt."""

    args = build_parser().parse_args()
    identity = compute_identity(args.raw_request, args.system_restatement)
    if args.action == "prepare":
        print(
            json.dumps(
                {
                    "INTENT_STATUS": "OWNER_CONFIRMATION_REQUIRED",
                    **identity,
                    "FILES_CHANGED": [],
                },
                sort_keys=True,
            )
        )
        return 0

    receipt_path, created = confirm_alignment(
        repo_root=args.repo_root,
        raw_request=args.raw_request,
        system_restatement=args.system_restatement,
        expected_intent_hash=_required_option(
            args.expected_intent_hash, "--expected-intent-hash"
        ),
        prior_challenge_hash=_required_option(
            args.prior_challenge_hash, "--prior-challenge-hash"
        ),
        confirmation=_required_option(args.confirmation, "--confirmation"),
        confirmation_evidence=_required_option(
            args.confirmation_evidence, "--confirmation-evidence"
        ),
        change_id=args.change_id,
        owner_problem=_required_option(args.owner_problem, "--owner-problem"),
        owner_visible_behavior=_required_option(
            args.owner_visible_behavior, "--owner-visible-behavior"
        ),
        invariants_scope=_required_option(
            args.invariants_scope, "--invariants-scope"
        ),
    )
    print(
        json.dumps(
            {
                "INTENT_STATUS": "ALIGNED",
                **identity,
                "RECEIPT": str(receipt_path),
                "CREATED": created,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AlignmentError as error:
        raise SystemExit(f"INTENT_ALIGNMENT_ERROR: {error}") from error
