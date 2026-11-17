"""Контрактные тесты надёжности супервизора (unit, temp sqlite)."""

from __future__ import annotations

import importlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from opsx_cli.supervisor import WorkflowSupervisor
from opsx_cli.supervisor_classify import classify_attempt
from opsx_cli.supervisor_journal import (
    JOURNAL_SCHEMA_VERSION,
    RecoveryContract,
    SupervisorJournal,
)
from opsx_cli.supervisor_meta import (
    FileFingerprint,
    foreign_dirty_paths,
    select_fix_commit_files,
)
from opsx_cli.supervisor_qualifier import qualify_failure
from opsx_cli.supervisor_reliability import (
    ConvergencePredicate,
    FailureDomain,
    RepairAuthority,
    derive_failure_domain,
    derive_repair_authority,
    entry_incident_resolved,
    evaluate_replay_convergence,
    map_fix_verdict_on_executor_failure,
    revisions_match,
    workflow_record_identity,
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

    def test_provider_internal_server_error(self) -> None:
        verdict, err = map_fix_verdict_on_executor_failure(
            "CANNOT_FIX",
            "InternalServerError: upstream",
        )
        self.assertEqual(verdict, "FIX_INTERRUPTED")

    def test_provider_silence_timeout(self) -> None:
        verdict, _ = map_fix_verdict_on_executor_failure(
            "CANNOT_FIX",
            "SILENCE_TIMEOUT: no chunks",
        )
        self.assertEqual(verdict, "FIX_INTERRUPTED")


class SupervisorIncidentLifecycleTests(unittest.TestCase):
    def test_commit_failed_not_incident_resolved(self) -> None:
        self.assertFalse(entry_incident_resolved("COMMIT_FAILED", "FAILED", None))
        self.assertFalse(entry_incident_resolved("FIXED", None, "PATCH_VERIFIED"))

    def test_committed_fixed_not_resolved_without_replay(self) -> None:
        self.assertFalse(
            entry_incident_resolved("FIXED", "COMMITTED", "COMMITTED"),
        )
        self.assertFalse(entry_incident_resolved("FIXED", "COMMITTED", None))

    def test_resolved_only_with_replay_phase(self) -> None:
        self.assertTrue(
            entry_incident_resolved("FIXED", "COMMITTED", "REPLAY_VERIFIED"),
        )


class SupervisorConvergenceUnitTests(unittest.TestCase):
    def test_wrong_revision_blocked(self) -> None:
        pending = {
            "incident_id": "e1",
            "execution_id": "run-a",
            "causal_signature": "sig",
            "before_failure_identity": "before",
            "repair_revision": "abc123def456",
            "original_code": "RED_MIXED_SUITE",
        }
        record = {"verification_status": "PASS", "exit_code": 0, "workflow_status": "PASS"}
        verdict = evaluate_replay_convergence(
            pending,
            record,
            run_id="run-a",
            attempt=2,
            current_revision="ffffffffffff",
        )
        self.assertFalse(verdict.incident_resolved)
        self.assertEqual(verdict.convergence_verdict, "REPLAY_REVISION_MISMATCH")
        self.assertEqual(verdict.supervisor_stop, "REPLAY_BLOCKED")

    def test_pass_without_incident_not_resolved(self) -> None:
        record = {"verification_status": "PASS", "exit_code": 0}
        verdict = evaluate_replay_convergence({}, record, run_id="r1", attempt=1, current_revision=None)
        self.assertFalse(verdict.incident_resolved)
        self.assertEqual(verdict.workflow_status, "SUPERVISED_PASS")

    def test_pass_without_commit_evidence_blocked(self) -> None:
        pending = {
            "incident_id": "e1",
            "execution_id": "run-a",
            "causal_signature": "sig",
            "before_failure_identity": "before",
            "repair_revision": None,
        }
        record = {"verification_status": "PASS", "exit_code": 0}
        verdict = evaluate_replay_convergence(
            pending,
            record,
            run_id="run-a",
            attempt=2,
            current_revision="abc",
        )
        self.assertEqual(verdict.convergence_verdict, "MISSING_REPAIR_REVISION")

    def test_original_cause_still_present_fails(self) -> None:
        pending = {
            "incident_id": "e1",
            "execution_id": "run-a",
            "causal_signature": "sig",
            "before_failure_identity": "before",
            "repair_revision": "abc123",
            "original_code": "RED_MIXED_SUITE",
        }
        record = {
            "verification_status": "PASS",
            "exit_code": 0,
            "error": "RED_MIXED_SUITE still logged",
        }
        verdict = evaluate_replay_convergence(
            pending,
            record,
            run_id="run-a",
            attempt=2,
            current_revision="abc123",
        )
        self.assertFalse(verdict.incident_resolved)
        self.assertEqual(verdict.convergence_verdict, "ORIGINAL_CAUSE_STILL_PRESENT")

    def test_regression_fails_convergence(self) -> None:
        before = workflow_record_identity({"workflow_status": "FAIL", "error": "x"})
        after = workflow_record_identity({"workflow_status": "PASS", "error": ""})
        pred = ConvergencePredicate("sig", before, "sha", "run:2")
        ok, code = pred.satisfied(
            after_failure_identity=after,
            replay_ok=True,
            original_cause_still_present=False,
            sibling_failure=False,
            regression=True,
        )
        self.assertFalse(ok)
        self.assertEqual(code, "REGRESSION")

    def test_revisions_match_short_prefix(self) -> None:
        self.assertTrue(revisions_match("abc123deadbeef", "abc123"))

    def test_no_progress_compares_causal_signature(self) -> None:
        pred = ConvergencePredicate("causal-a", "before", "rev", "ev")
        ok, code = pred.satisfied(
            after_failure_identity="after",
            replay_ok=True,
            original_cause_still_present=True,
            sibling_failure=False,
            regression=False,
        )
        self.assertFalse(ok)
        self.assertEqual(code, "ORIGINAL_CAUSE_STILL_PRESENT")


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

    def test_legacy_v1_schema_migrates_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "journal.sqlite"
            conn = sqlite3.connect(path)
            try:
                conn.execute(
                    "CREATE TABLE journal_events ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "recorded_at TEXT NOT NULL, payload_json TEXT NOT NULL)"
                )
                conn.execute(
                    "INSERT INTO journal_events (recorded_at, payload_json) VALUES (?, ?)",
                    ("2026-11-17T00:00:00Z", json.dumps({"record": "analysis", "what": "old"})),
                )
                conn.commit()
            finally:
                conn.close()
            journal = SupervisorJournal(path)
            rows = journal.list_entries()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].get("what"), "old")
            journal2 = SupervisorJournal(path)
            self.assertEqual(len(journal2.list_entries()), 1)

    def test_recovery_contract_persists_across_instances(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "journal.sqlite"
            journal = SupervisorJournal(path)
            contract = RecoveryContract(
                run_id="run-1",
                incident_id="inc-1",
                execution_id="run-1",
                original_causal_signature="sig",
                before_failure_identity="before",
                repair_revision="sha123",
            )
            journal.upsert_recovery_contract(contract)
            journal2 = SupervisorJournal(path)
            loaded = journal2.load_recovery_contract("run-1")
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.replay_status, "PENDING_REPLAY")
            journal2.clear_recovery_contract("run-1", convergence_verdict="CONVERGED")
            loaded2 = journal2.load_recovery_contract("run-1")
            assert loaded2 is not None
            self.assertEqual(loaded2.replay_status, "RESOLVED")
            journal.upsert_recovery_contract(contract)
            self.assertEqual(journal.load_recovery_contract("run-1").replay_status, "PENDING_REPLAY")


class SupervisorFixRunnerImportTests(unittest.TestCase):
    def test_missing_supervisor_reliability_fails_closed(self) -> None:
        saved = sys.modules.pop("opsx_cli.supervisor_reliability", None)
        saved_runner = sys.modules.pop(
            "opsx.opsx_runtime.agents.process_repair.fix_runner",
            None,
        )
        try:
            with self.assertRaises(ModuleNotFoundError):
                importlib.import_module(
                    "opsx.opsx_runtime.agents.process_repair.fix_runner",
                )
        finally:
            if saved is not None:
                sys.modules["opsx_cli.supervisor_reliability"] = saved
            if saved_runner is not None:
                sys.modules["opsx.opsx_runtime.agents.process_repair.fix_runner"] = saved_runner


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

    async def test_supervisor_restart_restores_pending_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "opsx" / "runs"
            sup = runs / "supervisor" / "restart-run"
            sup.mkdir(parents=True)
            journal_path = sup / "journal.sqlite"
            journal = SupervisorJournal(journal_path)
            journal.upsert_recovery_contract(
                RecoveryContract(
                    run_id="restart-run",
                    incident_id="e-restore",
                    execution_id="restart-run",
                    original_causal_signature="causal-restore",
                    before_failure_identity="identity-before",
                    repair_revision="deadbeef",
                ),
            )
            project = MagicMock()
            project.root = root
            project.core = root
            project.runs = runs
            service = MagicMock()
            service.project = project
            timeouts = SupervisorTimeouts.from_minutes(
                workflow_timeout_minutes=1,
                supervisor_timeout_minutes=5,
                max_attempts=3,
            )
            supervisor = WorkflowSupervisor(
                service,
                timeouts=timeouts,
                change_id="chg",
                run_id="restart-run",
            )
            self.assertIsNotNone(supervisor._pending_replay)
            self.assertEqual(
                supervisor._pending_replay.get("causal_signature"),
                "causal-restore",
            )


if __name__ == "__main__":
    unittest.main()