"""Тесты канонических путей opsx/logs и opsx/receipts."""

from __future__ import annotations

from pathlib import Path

from workflows.artifact_paths import (
    impl_verify_receipt_path,
    logs_dir,
    purge_change_runtime_artifacts,
    receipts_dir,
    run_log_path,
    session_id_from_stream_log,
)


def test_run_log_path_layout(tmp_path: Path) -> None:
    path = run_log_path(tmp_path, "demo-change", "abc123def456")
    assert path == tmp_path / "opsx" / "logs" / "demo-change" / "abc123def456.log"


def test_receipts_dir_layout(tmp_path: Path) -> None:
    assert receipts_dir(tmp_path, "visual") == tmp_path / "opsx" / "receipts" / "visual"
    assert impl_verify_receipt_path(tmp_path, "visual").name == "IMPL-VERIFY-RECEIPT.json"


def test_session_id_from_stream_log_variants() -> None:
    assert session_id_from_stream_log(r"opsx\logs\visual\abc123def456.log") == "abc123def456"
    assert (
        session_id_from_stream_log(
            "opsx/receipts/x/impl-verify-abc123def456.live.ndjson"
        )
        == "abc123def456"
    )


def test_purge_change_runtime_artifacts(tmp_path: Path) -> None:
    change = "purge-me"
    log_root = logs_dir(tmp_path, change)
    log_root.mkdir(parents=True)
    (log_root / "run.log").write_text("x", encoding="utf-8")
    receipt_root = receipts_dir(tmp_path, change)
    receipt_root.mkdir(parents=True)
    (receipt_root / "IMPL-VERIFY-RECEIPT.json").write_text("{}", encoding="utf-8")
    purge_change_runtime_artifacts(tmp_path, change)
    assert not logs_dir(tmp_path, change).exists()
    assert not receipt_root.exists()
