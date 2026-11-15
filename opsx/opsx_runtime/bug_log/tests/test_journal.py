"""Тесты идентичности, файла журнала и проекций."""

from __future__ import annotations

import asyncio
from pathlib import Path

from bug_log.file_journal import open_run_journal, require_live_journal
from bug_log.null_journal import NullBugJournal
from bug_log.fingerprint import fingerprint_pytest
from bug_log.lifecycle import apply_status
from bug_log.projector import BugJournalProjector
from bug_log.query import events_for_change
from bug_log.schemas import BugCandidate, BugObservation


def test_live_rejects_null_journal() -> None:
    try:
        require_live_journal(NullBugJournal(), live=True)
    except RuntimeError as exc:
        assert str(exc) == "LIVE_BUG_JOURNAL_REQUIRED"
    else:
        raise AssertionError("expected LIVE_BUG_JOURNAL_REQUIRED")


def test_fingerprint_pytest_stable() -> None:
    left = fingerprint_pytest("FAILED tests/a.py::test_x - AssertionError: boom")
    right = fingerprint_pytest("FAILED tests/a.py::test_x - AssertionError: boom")
    assert left == right


def test_status_anomaly_does_not_drop() -> None:
    status, anomaly = apply_status("FALSE_POSITIVE", "FIXING")
    assert status == "FALSE_POSITIVE"
    assert anomaly is True


def test_register_is_idempotent_across_runs(tmp_path: Path) -> None:
    async def _go() -> None:
        first = open_run_journal(repo_root=tmp_path, run_id="run-a", change_id="visual")
        second = open_run_journal(repo_root=tmp_path, run_id="run-b", change_id="visual")
        candidate = BugCandidate(
            lane="verify_gate",
            tool="pytest",
            observation="FAILED tests/a.py::test_x - AssertionError: boom",
            local_id="F-0001",
        )
        left = await first.register(candidate)
        right = await second.register(candidate)
        assert left.bug_id == right.bug_id
        assert left.bug_id.startswith("BUG-")

    asyncio.run(_go())


def test_change_shard_and_manifest(tmp_path: Path) -> None:
    async def _go() -> None:
        journal = open_run_journal(repo_root=tmp_path, run_id="run-v", change_id="visual")
        ref = await journal.register(
            BugCandidate(
                lane="verify_gate",
                tool="pytest",
                observation="FAILED tests/a.py::test_x - AssertionError: boom",
                local_id="F-0001",
                detail={"gate_code": "PYTEST_STALL"},
            )
        )
        await journal.record(
            BugObservation(bug_id=ref.bug_id, summary="diagnostic", status="CLASSIFIED")
        )
        report = BugJournalProjector(tmp_path).rebuild()
        assert report["events"] >= 2
        rows = events_for_change(tmp_path, "visual")
        assert rows
        assert all(row.get("change_id") == "visual" for row in rows)
        case = tmp_path / "opsx" / "runs" / "bug-cases" / f"BUGCASE-{ref.bug_id}" / "CASE.md"
        assert case.is_file()
        assert "CHANGE: visual" in case.read_text(encoding="utf-8")

    asyncio.run(_go())


def test_dedupe_same_sighting(tmp_path: Path) -> None:
    async def _go() -> None:
        journal = open_run_journal(repo_root=tmp_path, run_id="run-d", change_id="visual")
        candidate = BugCandidate(
            lane="owner_report",
            observation="миграция не работает",
            location="migration",
            local_id="owner-1",
        )
        await journal.register(candidate)
        await journal.register(candidate)
        text = (tmp_path / "opsx" / "runs" / "bug-log" / "BUG_JOURNAL.ndjson").read_text(encoding="utf-8")
        assert text.count('"kind": "BUG_REGISTERED"') == 1

    asyncio.run(_go())
