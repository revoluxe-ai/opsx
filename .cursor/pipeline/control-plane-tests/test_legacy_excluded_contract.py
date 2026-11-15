"""Негативные и позитивные проверки LEGACY_EXCLUDED terminal contract (PR #23)."""

from __future__ import annotations

from pathlib import Path

import pytest

from run_command_transfer_contract_tests import (
    COMMANDS,
    LEGACY_EXCLUDED_COMMAND_IDS,
    ROOT,
    TRANSFERRED_COMMANDS,
    TRANSFER_MANIFEST,
    check_command,
    check_legacy_excluded_commands,
    check_terminal_transfer_evidence,
    check_transfer_manifest,
    legacy_excluded_surface_rows,
    load_full_surface_manifest,
    parse_command_transfer_manifest,
    read_activation_rows,
    run_mandatory_transfer_acceptance_checks,
    verify_mandatory_transfer_batch_provenance,
)

REPO_ROOT = ROOT


def test_legacy_excluded_manifest_entry_target_absent() -> None:
    """Manifest must record LEGACY_EXCLUDED with target_present false."""

    rows = legacy_excluded_surface_rows(load_full_surface_manifest())
    assert set(rows) == set(LEGACY_EXCLUDED_COMMAND_IDS)
    for command_id in LEGACY_EXCLUDED_COMMAND_IDS:
        row = rows[command_id]
        assert row.get("disposition") == "LEGACY_EXCLUDED"
        assert row.get("target_present") is False
        assert row.get("target_sha256") is None


def test_legacy_excluded_command_file_absent() -> None:
    """Excluded commands must not materialize under .cursor/commands."""

    for command_id in LEGACY_EXCLUDED_COMMAND_IDS:
        assert not (COMMANDS / f"{command_id}.md").is_file()


def test_legacy_excluded_no_activation_row() -> None:
    """Excluded commands must not appear in command_activation.ndjson."""

    activation_ids = {row.get("command_id") for row in read_activation_rows()}
    for command_id in LEGACY_EXCLUDED_COMMAND_IDS:
        assert command_id not in activation_ids


def test_legacy_excluded_not_advertised_in_meta_router() -> None:
    """Meta orientation/router must not enable excluded slash commands."""

    orientation = (REPO_ROOT / "opsx/shared/meta-orientation.md").read_text(
        encoding="utf-8"
    )
    router = (REPO_ROOT / ".cursor/commands/opsx-meta.md").read_text(encoding="utf-8")
    for command_id in LEGACY_EXCLUDED_COMMAND_IDS:
        slash = f"/{command_id}"
        assert slash not in orientation
        assert slash not in router


def test_legacy_excluded_manifest_terminal_counts_consistent() -> None:
    """terminal_counts.LEGACY_EXCLUDED must match row cardinality."""

    manifest = load_full_surface_manifest()
    terminal_counts = manifest.get("terminal_counts")
    assert isinstance(terminal_counts, dict)
    rows = manifest.get("rows")
    assert isinstance(rows, list)
    row_total = sum(
        1
        for raw in rows
        if isinstance(raw, dict) and raw.get("disposition") == "LEGACY_EXCLUDED"
    )
    assert terminal_counts.get("LEGACY_EXCLUDED") == row_total


def test_active_bugbot_routes_remain_transferred() -> None:
    """Active bug ingress/orchestration routes stay installed-active."""

    for command_id in ("opsx-orch-bugbot-converge", "opsx-bug-intake-clear"):
        assert command_id in TRANSFERRED_COMMANDS
        assert (COMMANDS / f"{command_id}.md").is_file()
    manifest = parse_command_transfer_manifest()
    assert manifest["opsx-orch-bugbot-converge"] == "FUNCTIONAL_PROVEN"
    assert manifest["opsx-bug-intake-clear"] == "ACTIVE_WITH_CAPABILITY_GATES"


def test_missing_mandatory_transferred_command_not_masked() -> None:
    """LEGACY_EXCLUDED must not relax check_command for installed-active commands."""

    contract = TRANSFERRED_COMMANDS["opsx-doctor"]
    with pytest.raises(AssertionError, match="missing command"):
        check_command("__opsx-not-a-real-command__", contract)


def test_removed_installed_active_apply_fails_terminal_and_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Probe C: скрытие opsx-apply из activation и commands must fail composed gates."""

    import shutil

    shutil.copytree(ROOT / ".cursor" / "commands", tmp_path / "commands")
    (tmp_path / "commands" / "opsx-apply.md").unlink(missing_ok=True)

    filtered_rows = [
        row for row in read_activation_rows() if row.get("command_id") != "opsx-apply"
    ]

    def _filtered_activation() -> list[dict[str, object]]:
        return filtered_rows

    monkeypatch.setattr(
        "run_command_transfer_contract_tests.read_activation_rows",
        _filtered_activation,
    )
    monkeypatch.setattr(
        "run_command_transfer_contract_tests.COMMANDS",
        tmp_path / "commands",
    )

    with pytest.raises(AssertionError, match="opsx-apply"):
        check_terminal_transfer_evidence()

    with pytest.raises(AssertionError, match="opsx-apply"):
        run_mandatory_transfer_acceptance_checks()

    from opsx_dependency_closure import FailureRecord, _validate_installed_active_manifest_coverage

    failures: list[FailureRecord] = []
    _validate_installed_active_manifest_coverage(ROOT, filtered_rows, failures)
    assert any(item.command_id == "opsx-apply" for item in failures)


def test_missing_functional_proven_proof_registry_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Probe F: отсутствие active_command_activation_proof.json must fail terminal evidence."""

    import shutil

    shutil.copytree(ROOT / "opsx", tmp_path / "opsx", dirs_exist_ok=True)
    shutil.copytree(ROOT / ".cursor" / "commands", tmp_path / ".cursor" / "commands")
    proof_path = tmp_path / "opsx" / "registry" / "active_command_activation_proof.json"
    if proof_path.is_file():
        proof_path.unlink()

    monkeypatch.setattr("run_command_transfer_contract_tests.ROOT", tmp_path)
    monkeypatch.setattr(
        "run_command_transfer_contract_tests.COMMANDS",
        tmp_path / ".cursor" / "commands",
    )
    monkeypatch.setattr(
        "run_command_transfer_contract_tests.ACTIVATION",
        tmp_path / "opsx" / "registry" / "command_activation.ndjson",
    )
    monkeypatch.setattr(
        "run_command_transfer_contract_tests.TRANSFER_MANIFEST",
        tmp_path / "opsx" / "registry" / "command_transfer_manifest.md",
    )
    monkeypatch.setattr(
        "run_command_transfer_contract_tests.FULL_SURFACE_MANIFEST",
        tmp_path / "opsx" / "transfer" / "FULL_SURFACE_MANIFEST.json",
    )

    with pytest.raises(AssertionError, match="FUNCTIONAL_PROVEN activation proof"):
        check_terminal_transfer_evidence()


def test_stale_functional_proven_command_sha_invalidates_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FUNCTIONAL_PROVEN rows must bind proof command_sha256 to live command bytes."""

    import json
    import shutil

    shutil.copytree(ROOT / "opsx", tmp_path / "opsx", dirs_exist_ok=True)
    shutil.copytree(ROOT / ".cursor" / "commands", tmp_path / ".cursor" / "commands")
    proof_path = tmp_path / "opsx" / "registry" / "active_command_activation_proof.json"
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    for row in proof.get("commands", []):
        if row.get("command") == "opsx-orch-bug-fix":
            row["command_sha256"] = "0" * 64
            break
    proof_path.write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")

    monkeypatch.setattr("run_command_transfer_contract_tests.ROOT", tmp_path)
    monkeypatch.setattr(
        "run_command_transfer_contract_tests.COMMANDS",
        tmp_path / ".cursor" / "commands",
    )
    monkeypatch.setattr(
        "run_command_transfer_contract_tests.ACTIVATION",
        tmp_path / "opsx" / "registry" / "command_activation.ndjson",
    )
    monkeypatch.setattr(
        "run_command_transfer_contract_tests.TRANSFER_MANIFEST",
        tmp_path / "opsx" / "registry" / "command_transfer_manifest.md",
    )
    monkeypatch.setattr(
        "run_command_transfer_contract_tests.FULL_SURFACE_MANIFEST",
        tmp_path / "opsx" / "transfer" / "FULL_SURFACE_MANIFEST.json",
    )

    with pytest.raises(AssertionError, match="opsx-orch-bug-fix: FUNCTIONAL_PROVEN proof stale"):
        check_terminal_transfer_evidence()


def test_duplicate_manifest_rows_fail_check_transfer_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Дубликат строки ledger до dict collapse must fail manifest check."""

    body = TRANSFER_MANIFEST.read_text(encoding="utf-8")
    duped = body.replace(
        "| `opsx-archive` | `ACTIVE_WITH_CAPABILITY_GATES` |",
        "| `opsx-apply` | `LEGACY_EXCLUDED` |",
        1,
    )
    manifest = tmp_path / "command_transfer_manifest.md"
    _ = manifest.write_text(duped, encoding="utf-8")
    monkeypatch.setattr(
        "run_command_transfer_contract_tests.TRANSFER_MANIFEST",
        manifest,
    )
    with pytest.raises(AssertionError, match="duplicate command rows"):
        check_transfer_manifest()


def test_mandatory_provenance_missing_event_fails() -> None:
    """Отсутствие batch-события must fail-closed, not no-op."""

    with pytest.raises(AssertionError, match="expected one"):
        verify_mandatory_transfer_batch_provenance(
            commands=["__probe-nonexistent-command__"],
            command_hashes=["deadbeef"],
            label="probe",
        )


def test_production_legacy_excluded_contract_passes() -> None:
    """Integrated LEGACY_EXCLUDED proof on the production tree."""

    check_legacy_excluded_commands()
