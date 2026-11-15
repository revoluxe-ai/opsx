"""Focused tests for the target-native intent-alignment receipt helper."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[3]
HELPER = (
    Path(__file__).resolve().parents[1]
    / "intent-align"
    / "intent_alignment.py"
)
RAW_REQUEST = "Allow an Owner to confirm the product intent before planning."
RESTATEMENT = (
    "The Owner sees a plain-language restatement and explicitly confirms it "
    "before a durable alignment receipt exists."
)
OWNER_PROBLEM = "Prevent an unreviewed interpretation from becoming planning input."
OWNER_BEHAVIOR = "The Owner first sees the restatement, then confirms it."
BOUNDARIES = "No technical design and no planning-artifact writes."


def _load_helper() -> ModuleType:
    """Load the helper module from its hyphenated pipeline directory."""

    specification = importlib.util.spec_from_file_location(
        "opsx_intent_alignment", HELPER
    )
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _identity() -> tuple[str, str, str]:
    """Return independently calculated raw, restatement, and intent hashes."""

    raw_hash = hashlib.sha256(RAW_REQUEST.encode("utf-8")).hexdigest()
    restatement_hash = hashlib.sha256(RESTATEMENT.encode("utf-8")).hexdigest()
    intent_hash = hashlib.sha256(
        f"{raw_hash}:{restatement_hash}".encode("utf-8")
    ).hexdigest()
    return raw_hash, restatement_hash, intent_hash


def _run(
    repo_root: Path,
    action: str,
    *,
    confirmation: str | None = None,
    confirmation_evidence: str | None = None,
    expected_hash: str | None = None,
    challenge_hash: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Invoke the public helper CLI with one deterministic semantic payload."""

    command = [
        sys.executable,
        str(HELPER),
        action,
        "--repo-root",
        str(repo_root),
        "--raw-request",
        RAW_REQUEST,
        "--system-restatement",
        RESTATEMENT,
    ]
    if action == "confirm":
        _, _, intent_hash = _identity()
        command.extend(
            [
                "--expected-intent-hash",
                expected_hash or intent_hash,
                "--prior-challenge-hash",
                challenge_hash or intent_hash,
                "--confirmation",
                confirmation or "confirm",
                "--confirmation-evidence",
                confirmation_evidence or confirmation or "confirm",
                "--change-id",
                "unassigned",
                "--owner-problem",
                OWNER_PROBLEM,
                "--owner-visible-behavior",
                OWNER_BEHAVIOR,
                "--invariants-scope",
                BOUNDARIES,
            ]
        )
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_prepare_computes_identity_without_writing(tmp_path: Path) -> None:
    """Preparation emits only OWNER_CONFIRMATION_REQUIRED and changes no files."""

    result = _run(tmp_path, "prepare")
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    raw_hash, restatement_hash, intent_hash = _identity()
    assert output == {
        "FILES_CHANGED": [],
        "INTENT_HASH": intent_hash,
        "INTENT_STATUS": "OWNER_CONFIRMATION_REQUIRED",
        "RAW_REQUEST_SHA256": raw_hash,
        "SYSTEM_RESTATEMENT_SHA256": restatement_hash,
    }
    assert not (tmp_path / "opsx").exists()


def test_ambiguous_confirmation_never_writes(tmp_path: Path) -> None:
    """Bare process acknowledgements cannot create an ALIGNED receipt."""

    for token in ("GO", "OK", "proceed"):
        result = _run(
            tmp_path,
            "confirm",
            confirmation=token,
            confirmation_evidence=token,
        )
        assert result.returncode != 0
        assert "AMBIGUOUS_CONFIRMATION_TOKEN" in result.stderr
        assert not (tmp_path / "opsx").exists()


def test_stale_challenge_and_stale_expected_hash_are_rejected(
    tmp_path: Path,
) -> None:
    """Both the current identity and the prior visible challenge must match."""

    stale = "0" * 64
    stale_challenge = _run(tmp_path, "confirm", challenge_hash=stale)
    assert stale_challenge.returncode != 0
    assert "prior challenge" in stale_challenge.stderr

    stale_expected = _run(tmp_path, "confirm", expected_hash=stale)
    assert stale_expected.returncode != 0
    assert "expected hash" in stale_expected.stderr
    assert not (tmp_path / "opsx").exists()


def test_confirm_creates_one_exclusive_idempotent_receipt(tmp_path: Path) -> None:
    """Explicit confirmation creates one ALIGNED receipt and repeat is a no-op."""

    first = _run(tmp_path, "confirm", confirmation="подтверждаю")
    assert first.returncode == 0, first.stderr
    first_output = json.loads(first.stdout)
    _, _, intent_hash = _identity()
    receipt = tmp_path / "opsx" / "intents" / f"{intent_hash}.md"
    assert first_output["CREATED"] is True
    assert Path(first_output["RECEIPT"]) == receipt
    body_before = receipt.read_bytes()
    text = body_before.decode("utf-8")
    assert text.count("INTENT_STATUS:") == 1
    assert "INTENT_STATUS: ALIGNED" in text
    assert "TECHNICAL_DECISIONS_MADE: NONE" in text
    assert "INTENT_STATUS: TRANSFERRED" not in text

    second = _run(tmp_path, "confirm", confirmation="подтверждаю")
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout)["CREATED"] is False
    assert receipt.read_bytes() == body_before


def test_conflicting_or_transferred_existing_receipt_is_rejected(
    tmp_path: Path,
) -> None:
    """An occupied hash path cannot be overwritten or revived after transfer."""

    _, _, intent_hash = _identity()
    receipt = tmp_path / "opsx" / "intents" / f"{intent_hash}.md"
    receipt.parent.mkdir(parents=True)
    _ = receipt.write_text(
        "\n".join(
            [
                "# Intent Alignment Staging (Transferred)",
                "INTENT_STATUS: TRANSFERRED",
                "FREEZE_CAPABLE: false",
                f"INTENT_HASH: {intent_hash}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    before = receipt.read_bytes()
    result = _run(tmp_path, "confirm")
    assert result.returncode != 0
    assert "not exclusively ALIGNED" in result.stderr
    assert receipt.read_bytes() == before


def test_corrupted_aligned_receipt_is_not_treated_as_idempotent(
    tmp_path: Path,
) -> None:
    """A retained hash header cannot hide changed confirmed semantics."""

    first = _run(tmp_path, "confirm")
    assert first.returncode == 0, first.stderr
    receipt = Path(json.loads(first.stdout)["RECEIPT"])
    original = receipt.read_text(encoding="utf-8")
    corrupted = original.replace(RESTATEMENT, "A different, stale restatement.")
    _ = receipt.write_text(corrupted, encoding="utf-8")

    result = _run(tmp_path, "confirm")
    assert result.returncode != 0
    assert "conflicts in System Restatement" in result.stderr
    assert receipt.read_text(encoding="utf-8") == corrupted


def test_receipt_path_rejects_path_like_hash(tmp_path: Path) -> None:
    """Path containment accepts only a lowercase hexadecimal identity."""

    helper = _load_helper()
    resolver = getattr(helper, "resolve_receipt_path")
    try:
        _ = resolver(tmp_path, "..\\escape")
    except ValueError:
        return
    raise AssertionError("path-like hash was accepted")
