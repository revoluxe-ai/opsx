# -*- coding: utf-8 -*-
"""Проверки ADR Owner approval: pending не считается approval, подпись не выпускается."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(MODULE_DIR))

from check_adr_owner_approval import (  # noqa: E402
    APPROVAL_NOT_APPROVED,
    APPROVED,
    AUTHORIZED,
    INVALID,
    PENDING,
    PROMOTED_EARLY,
    SIGNATURE_MINTING,
    STALE,
    canonical_bytes,
    evaluate_approval,
    evaluate_archive_authorization,
    render_pending_approval,
)

FINGERPRINT = "HEAD=c5fff01ff93f9a480a837924830a8bc4db2619fe"
DECISION = """# Decision: sample

## Status

Proposed

## Decision

Keep the row.
"""
TOKEN = "OWNER-TOKEN-0123456789abcdef0123456789"


def _approval(text: str = DECISION, fingerprint: str = FINGERPRINT, status: str = "pending", signature: object = None) -> dict:
    document = render_pending_approval(
        change_id="telegram-lead-form",
        decision_id="ADR-001-sample",
        decision_path="openspec/changes/telegram-lead-form/decisions/ADR-001-sample/decision.md",
        decision_bytes=text.encode("utf-8"),
        worktree_fingerprint_value=fingerprint,
    )
    document["status"] = status
    document["owner_signature"] = signature
    return document


def _check(document: dict | None, text: str | None = DECISION, fingerprint: str = FINGERPRINT):
    return evaluate_approval(
        document,
        decision_text=text,
        change_id="telegram-lead-form",
        worktree_fingerprint_value=fingerprint,
    )


def test_pending_is_not_approval() -> None:
    """Unsigned template остаётся PENDING при совпавших hash и fingerprint."""
    result = _check(_approval())
    assert result.status == PENDING, result
    assert result.exit_code == 2


def test_no_signature_mint() -> None:
    """Рендер pending не заполняет owner_signature."""
    document = render_pending_approval(
        change_id="telegram-lead-form",
        decision_id="ADR-001-sample",
        decision_path="decisions/ADR-001-sample/decision.md",
        decision_bytes=DECISION.encode("utf-8"),
        worktree_fingerprint_value=FINGERPRINT,
    )
    assert SIGNATURE_MINTING is False
    assert document["owner_signature"] is None
    assert "def mint" not in Path(MODULE_DIR / "check_adr_owner_approval.py").read_text(encoding="utf-8")


def test_approved_when_owner_token_matches() -> None:
    """Чужой токен в тесте проверяет форму, не утверждает ADR-001."""
    result = _check(_approval(status="approved", signature=TOKEN))
    assert result.status == APPROVED, result


def test_placeholder_signature_rejected() -> None:
    """Короткое слово не проходит как Owner token."""
    result = _check(_approval(status="approved", signature="approved"))
    assert result.status == INVALID, result


def test_stale_on_decision_byte_change() -> None:
    """Другие байты decision делают evidence STALE."""
    document = _approval()
    changed = DECISION.replace("Keep the row.", "Keep another row.")
    result = _check(document, text=changed)
    assert result.status == STALE, result


def test_stale_on_fingerprint_change() -> None:
    """Другой HEAD делает evidence STALE."""
    result = _check(_approval(), fingerprint="HEAD=" + "ab" * 20)
    assert result.status == STALE, result


def test_accepted_inside_change_is_early_promotion() -> None:
    """Accepted в change-local файле не заменяет approval."""
    accepted = DECISION.replace("Proposed", "Accepted", 1)
    document = _approval(text=accepted, status="approved", signature=TOKEN)
    result = _check(document, text=accepted)
    assert result.status == PROMOTED_EARLY, result


def test_archive_authorization_is_separate() -> None:
    """Pending approval не открывает promotion даже при authorized-файле."""
    approval = _check(_approval())
    decisions = [
        {
            "decision_id": "ADR-001-sample",
            "decision_path": "openspec/changes/telegram-lead-form/decisions/ADR-001-sample/decision.md",
            "approval_path": "openspec/changes/telegram-lead-form/decisions/ADR-001-sample/owner-approval.json",
            "adr_content_hash": approval.adr_content_hash,
        }
    ]
    payload = {
        "schema_version": "adr-archive-authorization-v1",
        "change_id": "telegram-lead-form",
        "worktree_fingerprint": FINGERPRINT,
        "decisions": decisions,
    }
    document = {
        **payload,
        "status": "authorized",
        "owner_signature": TOKEN,
        "signed_canonical": canonical_bytes(payload).decode("utf-8"),
    }
    blocked = evaluate_archive_authorization(
        document,
        change_id="telegram-lead-form",
        worktree_fingerprint_value=FINGERPRINT,
        approval_results={"ADR-001-sample": approval},
    )
    assert blocked.status == APPROVAL_NOT_APPROVED, blocked
    opened = evaluate_archive_authorization(
        document,
        change_id="telegram-lead-form",
        worktree_fingerprint_value=FINGERPRINT,
        approval_results={"ADR-001-sample": _check(_approval(status="approved", signature=TOKEN))},
    )
    assert opened.status == AUTHORIZED, opened


def test_pending_archive_authorization_does_not_authorize() -> None:
    """status=pending у archive authorization не разрешает promotion."""
    approved = _check(_approval(status="approved", signature=TOKEN))
    decisions = [
        {
            "decision_id": "ADR-001-sample",
            "decision_path": "p",
            "approval_path": "a",
            "adr_content_hash": approved.adr_content_hash,
        }
    ]
    payload = {
        "schema_version": "adr-archive-authorization-v1",
        "change_id": "telegram-lead-form",
        "worktree_fingerprint": FINGERPRINT,
        "decisions": decisions,
    }
    document = {
        **payload,
        "status": "pending",
        "owner_signature": None,
        "signed_canonical": canonical_bytes(payload).decode("utf-8"),
    }
    result = evaluate_archive_authorization(
        document,
        change_id="telegram-lead-form",
        worktree_fingerprint_value=FINGERPRINT,
        approval_results={"ADR-001-sample": approved},
    )
    assert result.status == PENDING, result


def test_adr001_template_is_unsigned() -> None:
    """Закоммиченный шаблон ADR-001 pending и без подписи."""
    path = (
        REPO_ROOT
        / "openspec/changes/telegram-lead-form/decisions/ADR-001-telegram-lead-form-write-path/owner-approval.json"
    )
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["status"] == "pending"
    assert document["owner_signature"] is None
    assert document["change_id"] == "telegram-lead-form"
    assert document["worktree_fingerprint"] == FINGERPRINT
    assert document["signed_canonical"].encode("utf-8") == canonical_bytes(
        {
            "adr_content_hash": document["adr_content_hash"],
            "change_id": document["change_id"],
            "content_hash_algorithm": document["content_hash_algorithm"],
            "decision_id": document["decision_id"],
            "decision_path": document["decision_path"],
            "schema_version": document["schema_version"],
            "worktree_fingerprint": document["worktree_fingerprint"],
        }
    )


def test_command_rules_cite_the_contract() -> None:
    """Apply, verify и archive ссылаются на контракт и не авто-accept."""
    apply = (REPO_ROOT / ".cursor/commands/opsx-apply.md").read_text(encoding="utf-8")
    verify = (REPO_ROOT / ".cursor/commands/opsx-verify.md").read_text(encoding="utf-8")
    archive = (REPO_ROOT / ".cursor/commands/opsx-archive.md").read_text(encoding="utf-8")
    contract = ".cursor/pipeline/contracts/ADR_OWNER_APPROVAL.md"
    assert contract in apply
    assert contract in verify
    assert contract in archive
    assert "Apply does not set a change-local decision status to Accepted" in apply
    assert "Proposed ADR with matching Owner approval evidence is authorized" in verify
    assert "Promote only when verify mode=archive returned" in archive


def main() -> int:
    """Запускает проверки модуля и возвращает 0 при успехе."""
    tests = [
        test_pending_is_not_approval,
        test_no_signature_mint,
        test_approved_when_owner_token_matches,
        test_placeholder_signature_rejected,
        test_stale_on_decision_byte_change,
        test_stale_on_fingerprint_change,
        test_accepted_inside_change_is_early_promotion,
        test_archive_authorization_is_separate,
        test_pending_archive_authorization_does_not_authorize,
        test_adr001_template_is_unsigned,
        test_command_rules_cite_the_contract,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except AssertionError as exc:
            failed += 1
            sys.stderr.write(f"FAIL {test.__name__}: {exc}\n")
        else:
            sys.stdout.write(f"PASS {test.__name__}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
