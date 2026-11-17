"""Контрактные тесты надёжности супервизора (unit, temp sqlite)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from opsx_cli.supervisor import WorkflowSupervisor
from opsx_cli.supervisor_classify import classify_attempt
from opsx_cli.supervisor_journal import JOURNAL_SCHEMA_VERSION, SupervisorJournal
from opsx_cli.supervisor_meta import (
    FileFingerprint,
    foreign_dirty_paths,
    select_fix_commit_files,
)
from opsx_cli.supervisor_qualifier import qualify_failure
from opsx_cli.supervisor_reliability import (
    FailureDomain,
    RepairAuthority,
    derive_failure_domain,
    derive_repair_authority,
    entry_incident_resolved,
    map_fix_verdict_on_executor_failure,
)
from opsx_cli.supervisor_timeouts import SupervisorTimeouts
from opsx_cli.context import Project
from opsx_cli.service import RunService


class SupervisorDomainTests(unittest.TestCase):
  def test_opsx_core_import_error_not_product(self) -> None:
    record = {"error": "ModuleNotFoundError: No module named 'cursor_sdk'"}
    row = classify_attempt(record, log_tail="worker bootstrap")
    domain = derive_failure_domain(row, record)
    self.assertEqual(domain, FailureDomain.OPSX_CORE)
    authority = derive_repair_authority(domain, row)
    self.assertEqual(authority, RepairAuthority.META_REPAIR)

  def test_product_pytest_not_meta_repair(self) -> None:
    record = {"verification_status": "FAIL", "error": "named pytest checks failed"}
    tail = "FAILED tests/scenarios/foo.py::test_bar AssertionError"
    row = classify_attempt(record, log_tail=tail)
    domain = derive_failure_domain(row, record, log_tail=tail)
    self.assertEqual(domain, FailureDomain.PRODUCT_CODE)
    authority = derive_repair_authority(domain, row)
    self.assertEqual(authority, RepairAuthority.WORKFLOW_ONLY)
    verdict = qualify_failure(record, log_tail=tail)
    self.assertFalse(verdict.fix_hop_allowed)

  def test_red_mixed_suite_is_opsx_core(self) -> None:
    row = classify_attempt({"error": "RED_MIXED_SUITE"}, log_tail="receipt cli")
    self.assertEqual(derive_failure_domain(row, {}), FailureDomain.OPSX_CORE)

  def test_provider_timeout_not_cannot_fix(self) -> None:
    verdict, err = map_fix_verdict_on_executor_failure(
      "CANNOT_FIX",
      "APITimeoutError: bridge hung",
    )
    self.assertEqual(verdict, "FIX_INTERRUPTED")
    self.assertIn("REPAIR_EXECUTOR_UNAVAILABLE", err)


class SupervisorIncidentLifecycleTests(unittest.TestCase):
  def test_commit_failed_not_incident_resolved(self) -> None:
    self.assertFalse(entry_incident_resolved("COMMIT_FAILED", "FAILED", None))
    self.assertFalse(entry_incident_resolved("FIXED", None, "PATCH_VERIFIED"))

  def test_committed_fixed_not_resolved_without_replay(self) -> None:
    self.assertFalse(
      entry_incident_resolved("FIXED", "COMMITTED", "COMMITTED"),
    )


class SupervisorMetaBoundaryTests(unittest.TestCase):
  def test_foreign_dirty_blocks_commit_selection(self) -> None:
    meta = "opsx/opsx_runtime/agents/foo.py"
    foreign = "app/modules/billing.py"
    before = {
      f"core:{meta}": FileFingerprint(repo="core", path=meta, sha256="1"),
      f"core:{foreign}": FileFingerprint(repo="core", path=foreign, sha256="x"),
    }
    after = {
      f"core:{meta}": FileFingerprint(repo="core", path=meta, sha256="2"),
      f"core:{foreign}": FileFingerprint(repo="core", path=foreign, sha256="y"),
    }
    fix = {"verdict": "FIXED", "files_changed": [meta]}
    selected, err = select_fix_commit_files(fix, before, after)
    self.assertIsNotNone(err)
    self.assertIn("foreign dirty", err or "")
    self.assertEqual(selected, [])

  def test_foreign_dirty_paths_helper(self) -> None:
    before = {"core:a.py": FileFingerprint("core", "a.py", "1")}
    after = {
      "core:a.py": FileFingerprint("core", "a.py", "2"),
      "core:b.py": FileFingerprint("core", "b.py", "9"),
    }
    got = foreign_dirty_paths(before, after, {"core:a.py"})
    self.assertEqual(got, ["core:b.py"])


class SupervisorJournalMigrationTests(unittest.TestCase):
  def test_schema_v2_preserves_legacy_rows(self) -> None:
    with tempfile.TemporaryDirectory() as tmp:
      path = Path(tmp) / "journal.sqlite"
      journal = SupervisorJournal(path)
      journal.append_row({"record": "analysis", "kind": "QUALIFY", "what": "legacy"})
      rows = journal.list_entries()
      self.assertEqual(len(rows), 1)
      with journal.path.open("rb"):
        pass
      import sqlite3

      conn = sqlite3.connect(path)
      try:
        meta = conn.execute(
          "SELECT schema_version FROM journal_schema_meta WHERE id = 1",
        ).fetchone()
        self.assertIsNotNone(meta)
        self.assertEqual(int(meta[0]), JOURNAL_SCHEMA_VERSION)
        norm = conn.execute("SELECT COUNT(*) FROM journal_event_norm").fetchone()
        self.assertEqual(int(norm[0]), 1)
      finally:
        conn.close()


class SupervisorPartialExecutionTests(unittest.TestCase):
  def test_partial_meta_selection_still_returns_scope_error(self) -> None:
    meta = "opsx/opsx_runtime/agents/foo.py"
    before: dict = {}
    after = {f"core:{meta}": FileFingerprint("core", meta, "1")}
    fix = {"verdict": "FIXED", "files_changed": [meta]}
    selected, err = select_fix_commit_files(fix, before, after)
    self.assertIsNone(err)
    self.assertEqual(len(selected), 1)


class SupervisorConvergenceLoopTests(unittest.IsolatedAsyncioTestCase):
  async def test_repeat_causal_signature_stops_loop(self) -> None:
    service = RunService(Project.at("."))
    timeouts = SupervisorTimeouts.from_minutes(
      workflow_timeout_minutes=1,
      supervisor_timeout_minutes=5,
      max_attempts=5,
    )
    supervisor = WorkflowSupervisor(
      service,
      timeouts=timeouts,
      change_id="x",
      run_id="causal",
    )
    fail_record = {
      "verification_status": "FAIL",
      "exit_code": 1,
      "workflow_status": "FAILED",
      "error": "RED_MIXED_SUITE",
    }
    from opsx_cli.supervisor_journal import JournalEntry
    from opsx_cli.supervisor_qualifier import QualifierVerdict

    causal = "deadbeefdeadbeefdeadbeef"
    committed = JournalEntry(
      entry_id="e1",
      attempt=1,
      fingerprint="",
      classification={"causal_signature": causal},
      status="FIXED",
      created_at="",
      commit_status="COMMITTED",
    )

    calls = 0

    async def attempt(_: int) -> tuple[dict, str | None]:
      nonlocal calls
      calls += 1
      return fail_record, None

    async def fake_fix(_: int, __: object) -> JournalEntry:
      supervisor._fixed_progress_keys.add("RED_MIXED_SUITE|UNKNOWN_STAGE")
      supervisor._fixed_causal_signatures.add(causal)
      return committed

    qual = QualifierVerdict(
      owner="META",
      code="RED_MIXED_SUITE",
      stage="UNKNOWN_STAGE",
      fingerprint="fp",
      root_cause="",
      message="",
      fix_hop_allowed=True,
      failure_domain="OPSX_CORE",
      repair_authority="META_REPAIR",
      causal_signature=causal,
    )

    with patch.object(supervisor, "_workflow_attempt", side_effect=attempt):
      with patch.object(supervisor, "_run_fix_pipeline", side_effect=fake_fix):
        with patch.object(supervisor, "_run_qualify_pipeline", AsyncMock(return_value=qual)):
          result = await supervisor.run()
    self.assertEqual(result.get("supervisor_stop"), "NO_PROGRESS")
    self.assertEqual(calls, 2)


if __name__ == "__main__":
  unittest.main()