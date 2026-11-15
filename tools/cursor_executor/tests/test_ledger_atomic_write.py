"""Tests for ledger atomic JSON writes (Windows replace retry)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cursor_executor.ledger import LedgerStore, atomic_write_json


def test_atomic_write_json_persists_payload(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "record.json"
    atomic_write_json(target, {"execution_id": "x", "n": 1})
    parsed = json.loads(target.read_text(encoding="utf-8"))
    assert parsed["execution_id"] == "x"
    assert not target.with_suffix(target.suffix + ".tmp").exists()


def test_atomic_write_json_retries_transient_replace_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "record.json"
    calls = {"n": 0}
    real_replace = __import__("os").replace

    def _replace(src: str | Path, dst: str | Path) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError(5, "Отказано в доступе", str(dst), str(src))
        real_replace(src, dst)

    monkeypatch.setattr("cursor_executor.ledger.os.replace", _replace)

    def _noop_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr("cursor_executor.ledger.time.sleep", _noop_sleep)

    atomic_write_json(target, {"ok": True})
    assert calls["n"] == 2
    assert json.loads(target.read_text(encoding="utf-8"))["ok"] is True


def test_ledger_store_save_record_uses_atomic_write(tmp_path: Path) -> None:
    store = LedgerStore(root=tmp_path)
    from cursor_executor.ledger import LedgerRecord

    record = LedgerRecord(
        execution_id="exec-1",
        request_digest="d",
        workspace=".",
        lifecycle_phase="PREPARING",
        adapter_terminal=None,
        agent_id=None,
        run_id=None,
        requested_route={},
        validated_route=None,
        observed_route=None,
        created_at="t0",
        updated_at="t0",
    )
    store.save_record(record)
    path = tmp_path / "exec-1" / "record.json"
    assert path.is_file()
