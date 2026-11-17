"""Unit tests for workflow supervisor controller."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from opsx_cli.context import OpsxError, Project
from opsx_cli.supervisor_classify import FailureOwner, classify_attempt
from opsx_cli.supervisor_qualifier import qualify_failure
from opsx_cli.supervisor_meta import validate_meta_write_paths, FileFingerprint
from opsx_cli.supervisor_timeouts import SupervisorTimeouts


class SupervisorTimeoutTests(unittest.TestCase):
    def test_invalid_supervisor_less_than_workflow(self) -> None:
        with self.assertRaises(OpsxError) as ctx:
            SupervisorTimeouts.from_minutes(workflow_timeout_minutes=60, supervisor_timeout_minutes=30)
        self.assertEqual(ctx.exception.code, "TIMEOUT_CONFIG_INVALID")

    def test_minutes_to_seconds(self) -> None:
        limits = SupervisorTimeouts.from_minutes(workflow_timeout_minutes=45, supervisor_timeout_minutes=180)
        self.assertEqual(limits.workflow_timeout_seconds, 45 * 60)
        self.assertEqual(limits.supervisor_timeout_seconds, 180 * 60)


class SupervisorClassifyTests(unittest.TestCase):
    def test_meta_read_scope_is_repair_eligible(self) -> None:
        record = {"verification_status": "FAIL", "workflow_status": "FAILED"}
        tail = "READ_SCOPE_VIOLATION on opsx/opsx_runtime/agents/config.py"
        row = classify_attempt(record, log_tail=tail)
        self.assertEqual(row.owner, FailureOwner.META)
        self.assertTrue(row.repair_eligible)

    def test_infra_not_repair_eligible(self) -> None:
        record = {"error": "CURSOR_PROVIDER_UNAVAILABLE"}
        row = classify_attempt(record, log_tail="")
        self.assertEqual(row.owner, FailureOwner.INFRA)
        self.assertFalse(row.repair_eligible)


class SupervisorAutonomyClassifyTests(unittest.TestCase):
    """Остановка линии из-за недосданного артефакта — дефект процесса, а не Owner."""

    def test_lane_terminal_apply_blocked_is_meta(self) -> None:
        tail = (
            "ImplementationLaneTerminalError: APPLY_BLOCKED stage=apply_design\n"
            "RUN_STATUS_PAUSED reason=artifact_missing DESIGN_MACHINE_GATE_FAILED"
        )
        row = classify_attempt({"error": "ImplementationLaneTerminalError"}, log_tail=tail)
        self.assertEqual(row.owner, FailureOwner.META)
        self.assertTrue(row.repair_eligible)

    def test_own_log_banner_is_not_the_failure_code(self) -> None:
        tail = (
            '{"opsx_event": {"event": "SUPERVISOR_ATTEMPT"}}\n'
            "supervisor: попытка 1 в том же запуске\n"
            "OpsxError: A project execution or installation is active."
        )
        row = classify_attempt({"verification_status": "FAIL"}, log_tail=tail)
        self.assertEqual(row.owner, FailureOwner.INFRA)
        self.assertNotEqual(row.code, "SUPERVISOR_ATTEMPT")

    def test_explicit_owner_marker_stops(self) -> None:
        row = classify_attempt({}, log_tail="BLOCKED_NEEDS_OWNER: UNRESOLVED_PRODUCT_SEMANTICS")
        self.assertEqual(row.owner, FailureOwner.OWNER)
        self.assertFalse(row.repair_eligible)

    def test_missing_trace_matrix_before_materialize_is_meta(self) -> None:
        err = (
            "FileNotFoundError: [Errno 2] No such file or directory: "
            "'C:\\\\Users\\\\vkita\\\\my_code\\\\adv-bot\\\\opsx\\\\receipts\\\\"
            "new-tariffs\\\\scenario-coverage\\\\test_trace_matrix.json'"
        )
        row = classify_attempt(
            {"verification_status": "FAIL", "workflow_status": "UNKNOWN", "error": err},
            log_tail="",
        )
        self.assertEqual(row.owner, FailureOwner.META)
        self.assertTrue(row.repair_eligible)
        self.assertEqual(row.code, "FileNotFoundError")

    def test_missing_bug_log_manifest_is_meta(self) -> None:
        err = (
            "FileNotFoundError: [Errno 2] No such file or directory: "
            "'C:\\\\Users\\\\vkita\\\\my_code\\\\adv-bot\\\\opsx\\\\runs\\\\bug-log\\\\"
            "ANALYSIS_MANIFEST.json'"
        )
        row = classify_attempt({"verification_status": "FAIL", "error": err}, log_tail="")
        self.assertEqual(row.owner, FailureOwner.META)
        self.assertTrue(row.repair_eligible)

    def test_named_pytest_failure_stays_product(self) -> None:
        row = classify_attempt(
            {"verification_status": "FAIL", "error": "named pytest checks failed after fix"},
            log_tail="FAILED tests/scenarios/foo.py::test_bar",
        )
        self.assertEqual(row.owner, FailureOwner.PRODUCT)
        self.assertFalse(row.repair_eligible)

    def test_qualifier_receipt_cli_timeout_meta_fix_allowed(self) -> None:
        err = (
            "subprocess.TimeoutExpired: Command '['node', '--import', "
            "'file:///C:/hook.mjs', 'opsx-record-slice-receipt-decision-cli.mjs']' "
            "timed out after 180 seconds"
        )
        row = qualify_failure(
            {
                "verification_status": "FAIL",
                "workflow_status": "STAGE_APPLY_RED",
                "error": err,
            },
            log_tail="LangGraph node slice_apply_red",
        )
        self.assertEqual(row.owner, "META")
        self.assertTrue(row.fix_hop_allowed)
        self.assertIn("receipt", row.root_cause.lower())

    def test_qualifier_hang_or_abnormal_exit_is_process(self) -> None:
        row = qualify_failure(
            {
                "verification_status": "FAIL",
                "workflow_status": "STAGE_APPLY_RED",
                "process_status": "FAILED",
                "error": "AssertionError: boom during hop",
            },
            log_tail="FAILED tests/scenarios/foo.py::test_bar AssertionError",
        )
        self.assertEqual(row.owner, "META")
        self.assertTrue(row.fix_hop_allowed)

    def test_qualifier_clean_terminal_acceptance_fail_opens_solver(self) -> None:
        row = qualify_failure(
            {
                "verification_status": "FAIL",
                "workflow_status": "WORKFLOW_TERMINAL",
                "process_status": "COMPLETED",
                "exit_code": 1,
                "error": "verify: change did not meet product acceptance",
            },
            log_tail="coverage gap on spec clause",
        )
        self.assertEqual(row.owner, "META")
        self.assertTrue(row.fix_hop_allowed)
        self.assertIn("OPSX gap", row.root_cause)

    def test_receipt_cli_subprocess_timeout_is_meta(self) -> None:
        err = (
            "subprocess.TimeoutExpired: Command '['node', '--import', "
            "'file:///C:/hook.mjs', 'opsx-record-slice-receipt-decision-cli.mjs']' "
            "timed out after 180 seconds"
        )
        row = classify_attempt(
            {
                "verification_status": "FAIL",
                "workflow_status": "STAGE_APPLY_RED",
                "error": err,
            },
            log_tail="LangGraph node slice_apply_red",
        )
        self.assertEqual(row.owner, FailureOwner.META)
        self.assertTrue(row.repair_eligible)


class SupervisorJournalDeadEndsTests(unittest.TestCase):
    def test_current_run_rejected_fix_is_visible(self) -> None:
        import tempfile

        from opsx_cli.supervisor_journal import JournalEntry, SupervisorJournal

        with tempfile.TemporaryDirectory() as tmp:
            journal = SupervisorJournal(Path(tmp) / "run1" / "journal.sqlite")
            journal.append(JournalEntry(
                entry_id="e1", attempt=1, fingerprint="f", classification={"code": "APPLY_BLOCKED"},
                status="FIX_REJECTED", created_at="", error="outside allowlist",
                files_changed=[{"repo": "core", "path": "opsx_cli/x.py", "sha256": "a"}],
                change_id="chg",
            ))
            rows = journal.prior_dead_ends("chg")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["kind"], "FIX_REJECTED")
            self.assertEqual(rows[0]["files"], ["opsx_cli/x.py"])
            self.assertEqual(journal.prior_dead_ends("other"), [])


class SupervisorSolverChoiceJournalTests(unittest.TestCase):
    def test_journal_records_repair_agent(self) -> None:
        import tempfile

        from opsx_cli.supervisor import journal_solver_choice
        from opsx_cli.supervisor_journal import SupervisorJournal

        with tempfile.TemporaryDirectory() as tmp:
            journal = SupervisorJournal(Path(tmp) / "run1" / "journal.sqlite")
            journal_solver_choice(
                journal,
                fix_result={
                    "verdict": "FIXED",
                    "solver_action": "REPAIR_AGENT",
                    "repair_agent": "apply_design",
                    "root_cause": "prompt omits receipt field",
                    "files_changed": [
                        "opsx/opsx_runtime/agents/implementation/apply_design/prompt/get_prompt.py",
                    ],
                },
                change_id="chg",
                run_id="run1",
                attempt=1,
                entry_id="e1",
            )
            rows = journal.list_entries()
            choice_rows = [r for r in rows if r.get("kind") == "SOLVER_CHOICE"]
            self.assertEqual(len(choice_rows), 1)
            self.assertEqual(choice_rows[0]["solver_action"], "REPAIR_AGENT")
            self.assertEqual(choice_rows[0]["repair_agent"], "apply_design")
            self.assertIn("REPAIR_AGENT", choice_rows[0]["what"])

    def test_create_agent_is_never_emitted(self) -> None:
        import tempfile

        from opsx_cli.supervisor import journal_solver_choice
        from opsx_cli.supervisor_journal import SupervisorJournal

        with tempfile.TemporaryDirectory() as tmp:
            journal = SupervisorJournal(Path(tmp) / "run2" / "journal.sqlite")
            choice = journal_solver_choice(
                journal,
                fix_result={
                    "verdict": "CANNOT_FIX",
                    "solver_action": "CREATE_AGENT",
                    "create_pattern": "apply_red",
                    "create_agent_name": "apply_red_receipt_helper",
                    "root_cause": "attempted new agent",
                },
                change_id="chg",
                run_id="run2",
                attempt=2,
                entry_id="e2",
            )
            self.assertIsNone(choice)
            rows = journal.list_entries()
            for row in rows:
                self.assertNotEqual(row.get("solver_action"), "CREATE_AGENT")
            rejected = [r for r in rows if r.get("kind") == "SOLVER_CHOICE"]
            self.assertEqual(len(rejected), 1)
            self.assertIn("rejected", rejected[0].get("what", "").lower())


class SupervisorFixRoundJournalTests(unittest.TestCase):
    def test_empty_diff_still_has_why(self) -> None:
        from opsx_cli.supervisor import describe_fix_round

        what, why = describe_fix_round(
            verdict="CANNOT_FIX", root_cause="", error="", files=[], status="FIX_REJECTED",
        )
        self.assertIn("files=0", what)
        self.assertIn("ничего не изменено", why)
        self.assertIn("не назвал причину", why)

    def test_named_cause_is_kept(self) -> None:
        from opsx_cli.supervisor import describe_fix_round

        _what, why = describe_fix_round(
            verdict="CANNOT_FIX",
            root_cause="валидатор требует planning_digest, промпт его не упоминает",
            error="",
            files=[],
            status="FIX_REJECTED",
        )
        self.assertIn("planning_digest", why)


class SupervisorNamedCheckPytestEnvTests(unittest.TestCase):
    def test_named_check_subprocess_imports_agents_package(self) -> None:
        import subprocess
        from pathlib import Path

        from opsx_cli.supervisor_meta import (
            opsx_named_check_subprocess_env,
            resolve_opsx_named_check_interpreter,
            resolve_opsx_runtime_package_root,
        )

        core = Path(__file__).resolve().parents[1]
        runtime = resolve_opsx_runtime_package_root(core)
        self.assertTrue((runtime / "agents").is_dir(), msg=str(runtime))
        env = opsx_named_check_subprocess_env(core)
        py = resolve_opsx_named_check_interpreter(core)
        completed = subprocess.run(
            [str(py), "-c", "import agents; print(agents.__name__)"],
            cwd=core,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=(completed.stdout or "") + (completed.stderr or ""),
        )


class SupervisorRerunFixChecksTests(unittest.IsolatedAsyncioTestCase):
    async def test_rerun_fix_checks_collects_opsx_runtime_agent_tests(self) -> None:
        """Контроллерный перезапуск checks_run: Poetry opsx/core + PYTHONPATH на agents."""
        from opsx_cli.service import RunService

        repo_root = Path(__file__).resolve().parents[3]
        service = RunService(Project.at(repo_root))
        node_id = (
            "opsx/opsx_runtime/agents/tests/implementation/scenario_test_author/"
            "test_authoring_parent_reconcile.py"
        )
        results = await service.rerun_fix_checks(
            {"checks_run": [{"node_id": node_id}]},
            timeout_seconds=120.0,
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["node_id"], node_id)
        self.assertEqual(
            results[0].get("exit_code"),
            0,
            msg=results[0].get("tail") or results[0].get("error"),
        )


class ResolveOpsxPytestTargetTests(unittest.TestCase):
    def test_named_check_resolves_under_core_opsx_tree(self) -> None:
        from pathlib import Path

        from opsx_cli.supervisor_meta import resolve_opsx_pytest_target

        core = Path(__file__).resolve().parents[1]
        node_id = "opsx/opsx_runtime/agents/common/tests/test_contract_prompts_mount.py"
        target = resolve_opsx_pytest_target(core, node_id)
        self.assertTrue(target.is_file(), msg=str(target))
        self.assertIn("opsx_runtime", target.parts)

    def test_named_check_with_pytest_node_suffix(self) -> None:
        from pathlib import Path

        from opsx_cli.supervisor_meta import format_opsx_pytest_arg, resolve_opsx_pytest_target

        core = Path(__file__).resolve().parents[1]
        node_id = (
            "opsx/opsx_runtime/workflows/implementation/tests/"
            "test_stage_receipt_writer_hop_decision.py::test_scaffold_dispatch_success_without_model_paths"
        )
        target = resolve_opsx_pytest_target(core, node_id)
        self.assertTrue(target.is_file(), msg=str(target))
        arg = format_opsx_pytest_arg(core, node_id)
        self.assertIn("::test_scaffold_dispatch_success_without_model_paths", arg)
        self.assertTrue(arg.startswith(str(target)))


class SupervisorWriteFenceTests(unittest.TestCase):
    def test_product_read_ok_product_edit_rejected(self) -> None:
        from agents.config import check_paths_in_scope, resolve_read_scope

        scope = resolve_read_scope("PROCESS_SUPERVISOR_FIX")
        read = check_paths_in_scope(scope, tool_name="read", paths=["app/modules/x.py"])
        edit_product = check_paths_in_scope(scope, tool_name="edit", paths=["app/modules/x.py"])
        edit_artifact = check_paths_in_scope(
            scope, tool_name="edit", paths=["openspec/changes/new-tariffs/tasks.md"],
        )
        edit_opsx = check_paths_in_scope(
            scope,
            tool_name="edit",
            paths=["opsx/core/opsx/opsx_runtime/agents/config.py"],
        )
        self.assertEqual(read.violations, [])
        self.assertTrue(edit_product.violations)
        self.assertTrue(edit_artifact.violations)
        self.assertEqual(edit_opsx.violations, [])
        core_grep = check_paths_in_scope(
            scope,
            tool_name="grep",
            paths=["opsx/core"],
        )
        phantom = check_paths_in_scope(
            scope,
            tool_name="glob",
            paths=["opsx/contract_prompts"],
        )
        self.assertEqual(core_grep.violations, [])
        self.assertTrue(phantom.violations)
        self.assertEqual(phantom.violations[0].rule, "outside_restrict_allow")
        for self_path in (
            "opsx/core/opsx_cli/supervisor.py",
            "opsx/core/opsx_cli/supervisor_journal.py",
            "opsx/core/opsx/opsx_runtime/agents/process_repair/fix_runner.py",
            "opsx/core/opsx/contract_prompts/process_supervisor/FIX_AGENT.md",
            "opsx/core/tests/test_supervisor.py",
        ):
            blocked = check_paths_in_scope(scope, tool_name="edit", paths=[self_path])
            self.assertTrue(blocked.violations, msg=self_path)
            self.assertTrue(blocked.violations[0].rule.startswith("deny:"), msg=self_path)
        other = check_paths_in_scope(
            scope,
            tool_name="edit",
            paths=["opsx/core/opsx/opsx_runtime/agents/config.py"],
        )
        self.assertEqual(other.violations, [])


class SupervisorModelPresetTests(unittest.TestCase):
    def test_supervisor_uses_grok_47_medium_standard(self) -> None:
        from agents.adapters.hop_model_selection import supervisor_fix_model_selection

        selection = supervisor_fix_model_selection()
        self.assertEqual(selection["id"], "grok-4.7")
        params = {row["id"]: row["value"] for row in selection["params"]}
        self.assertEqual(params["reasoning_effort"], "medium")
        self.assertEqual(params["fast"], "false")
        self.assertEqual(params["context"], "256k")


class SiteStatusTests(unittest.TestCase):
    def test_attempt_failure_stays_running_without_error(self) -> None:
        from opsx_cli.site_status import keep_site_running, site_shows_error

        visible = keep_site_running({
            "status": "FAIL",
            "error": "ImplementationLaneTerminalError",
            "verification_status": "FAIL",
        })
        self.assertEqual(visible["status"], "RUNNING")
        self.assertNotIn("error", visible)
        self.assertFalse(site_shows_error(visible))

    def test_timeout_and_crash_are_the_site_error(self) -> None:
        from opsx_cli.site_status import site_shows_error

        self.assertTrue(site_shows_error({"workflow_status": "SUPERVISOR_TIMEOUT"}))
        self.assertTrue(site_shows_error({"supervisor_stop": "EXCEPTION", "error": "boom"}))
        self.assertFalse(site_shows_error({
            "status": "FAILED",
            "supervisor_stop": "OWNER",
            "error": "needs a decision",
        }))


class SupervisorFixToolsTests(unittest.TestCase):
    def test_fix_tools_are_accepted_by_the_repair_adapter(self) -> None:
        from agents.adapters.repair_tool_policy import ALLOWED_TOOLS
        from agents.process_repair.fix_runner import SUPERVISOR_FIX_TOOLS

        self.assertNotIn("run_opsx_pytest", SUPERVISOR_FIX_TOOLS)
        self.assertNotIn("shell", SUPERVISOR_FIX_TOOLS)
        self.assertTrue(set(SUPERVISOR_FIX_TOOLS) <= set(ALLOWED_TOOLS))

    def test_supervisor_contract_loader_and_no_shell_rule(self) -> None:
        import importlib.util

        core_root = Path(__file__).resolve().parents[1]
        loader_path = (
            core_root / "opsx/contract_prompts/process_supervisor/loader.py"
        )
        spec = importlib.util.spec_from_file_location(
            "opsx_process_supervisor_loader", loader_path
        )
        assert spec is not None and spec.loader is not None
        loader_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loader_mod)
        text = loader_mod.load_supervisor_orchestration_contract(core_root)
        self.assertIn("нет shell", text)
        self.assertIn("никогда", text.lower())
        self.assertIn("shell", text)

    def test_review_tools_never_grant_shell(self) -> None:
        import sys

        core_root = Path(__file__).resolve().parents[1]
        runtime_root = core_root / "opsx/opsx_runtime"
        if str(runtime_root) not in sys.path:
            sys.path.insert(0, str(runtime_root))
        from agents.adapters.cursor_sdk.cursor_sdk_adapter import REVIEW_TOOLS

        self.assertNotIn("shell", REVIEW_TOOLS)

    def test_opsx_pytest_tool_matches_custom_tool_spec(self) -> None:
        from agents.agent_tools.deterministic.opsx_pytest_tool import (
            build_run_opsx_pytest_custom_tool,
        )

        specs = build_run_opsx_pytest_custom_tool(core_root=Path("."))
        spec = specs["run_opsx_pytest"]
        self.assertTrue(callable(spec.execute))
        self.assertIn("node_id", spec.input_schema["properties"])


class SupervisorMetaValidationTests(unittest.TestCase):
    def test_reject_product_outside_core(self) -> None:
        files = [FileFingerprint(repo="project", path="src/foo.py", sha256="a")]
        ok, reason = validate_meta_write_paths(files, dirty_at_start=set(), core_root=Path("."))
        self.assertFalse(ok)
        self.assertIn("outside core", reason)

    def test_reject_supervisor_deny_path(self) -> None:
        files = [FileFingerprint(repo="core", path="opsx_cli/supervisor.py", sha256="a")]
        ok, reason = validate_meta_write_paths(files, dirty_at_start=set(), core_root=Path("."))
        self.assertFalse(ok)
        self.assertIn("deny", reason)

    def test_reject_fix_agent_and_its_prompt(self) -> None:
        for path in (
            "opsx/opsx_runtime/agents/process_repair/fix_runner.py",
            "opsx/contract_prompts/process_supervisor/FIX_AGENT.md",
            "tests/test_supervisor.py",
        ):
            files = [FileFingerprint(repo="core", path=path, sha256="a")]
            ok, reason = validate_meta_write_paths(files, dirty_at_start=set(), core_root=Path("."))
            self.assertFalse(ok, msg=path)
            self.assertIn("deny", reason)

    def test_allow_preexisting_dirty_on_meta_allowlist(self) -> None:
        path = "opsx/opsx_runtime/workflows/implementation/stage_receipt_writer.py"
        dirty_key = f"core:{path}"
        files = [FileFingerprint(repo="core", path=path, sha256="b")]
        ok, reason = validate_meta_write_paths(
            files, dirty_at_start={dirty_key}, core_root=Path(".")
        )
        self.assertTrue(ok, msg=reason)

    def test_allow_opsx_tools_receipt_cli(self) -> None:
        """Core receipt materializers live under ``opsx_tools/``, not ``opsx/opsx_tools/``."""
        path = "opsx_tools/opsx-record-slice-receipt-decision-cli.mjs"
        files = [FileFingerprint(repo="core", path=path, sha256="a")]
        ok, reason = validate_meta_write_paths(files, dirty_at_start=set(), core_root=Path("."))
        self.assertTrue(ok, msg=reason)

    def test_reject_preexisting_dirty_outside_meta_allowlist(self) -> None:
        path = "README.md"
        dirty_key = f"core:{path}"
        files = [FileFingerprint(repo="core", path=path, sha256="b")]
        ok, reason = validate_meta_write_paths(
            files, dirty_at_start={dirty_key}, core_root=Path(".")
        )
        self.assertFalse(ok)
        self.assertIn("pre-existing dirty", reason)

    def test_fingerprints_from_fix_report_when_diff_empty(self) -> None:
        from opsx_cli.supervisor_meta import fingerprints_for_fix_report_paths

        rel = "opsx/opsx_runtime/workflows/implementation/receipt_cli_identity_loader.mjs"
        fps = {
            f"core:{rel}": FileFingerprint(repo="core", path=rel, sha256="abc"),
        }
        fix = {
            "files_changed": [
                f"opsx/core/{rel}",
                rel,
            ]
        }
        got = fingerprints_for_fix_report_paths(fix, fps)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].sha256, "abc")

    def test_canonical_journal_repo_path_maps_fix_hop_to_core(self) -> None:
        from opsx_cli.supervisor_meta import canonical_journal_repo_path

        rel = "opsx/opsx_runtime/workflows/implementation/receipt_cli_identity_hook.mjs"
        self.assertEqual(canonical_journal_repo_path("project", rel), ("core", rel))
        prefixed = f"opsx/core/{rel}"
        self.assertEqual(canonical_journal_repo_path("project", prefixed), ("project", prefixed))
        self.assertEqual(
            canonical_journal_repo_path("core", prefixed),
            ("core", rel),
        )

    def test_select_fix_commit_files_ignores_unrelated_dirty_hop(self) -> None:
        from opsx_cli.supervisor_meta import select_fix_commit_files

        meta = "opsx/opsx_runtime/foo.py"
        noise = ".cursor/rules/opsx.mdc"
        before = {
            f"core:{meta}": FileFingerprint(repo="core", path=meta, sha256="1"),
            f"core:{noise}": FileFingerprint(repo="core", path=noise, sha256="2"),
        }
        after = {
            f"core:{meta}": FileFingerprint(repo="core", path=meta, sha256="1a"),
            f"core:{noise}": FileFingerprint(repo="core", path=noise, sha256="2b"),
        }
        fix = {"verdict": "FIXED", "files_changed": [meta]}
        selected, err = select_fix_commit_files(fix, before, after)
        self.assertIsNotNone(err)
        self.assertIn("foreign dirty", err or "")
        self.assertEqual(selected, [])


class SupervisorLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_pass_on_first_attempt(self) -> None:
        project = Project.at(".")
        from opsx_cli.service import RunService
        from opsx_cli.supervisor import WorkflowSupervisor

        service = RunService(project)
        timeouts = SupervisorTimeouts.from_minutes(workflow_timeout_minutes=1, supervisor_timeout_minutes=5, max_attempts=3)
        supervisor = WorkflowSupervisor(
            service,
            timeouts=timeouts,
            change_id="visual",
            run_id="abc123",
        )
        pass_record = {
            "verification_status": "PASS",
            "exit_code": 0,
            "workflow_status": "WORKFLOW_TERMINAL",
        }

        with patch.object(supervisor, "_workflow_attempt", AsyncMock(return_value=(pass_record, None))):
            with patch.object(supervisor, "_run_fix_pipeline", AsyncMock()) as fix_mock:
                result = await supervisor.run()
        self.assertEqual(result["status"], "PASS")
        fix_mock.assert_not_called()

    async def test_supervisor_timeout_before_attempt(self) -> None:
        import time

        from opsx_cli.service import RunService
        from opsx_cli.supervisor import WorkflowSupervisor

        service = RunService(Project.at("."))
        timeouts = SupervisorTimeouts.from_minutes(workflow_timeout_minutes=10, supervisor_timeout_minutes=10, max_attempts=5)
        supervisor = WorkflowSupervisor(service, timeouts=timeouts, change_id="x", run_id="deadbeef")
        supervisor._deadline = time.monotonic() - 1
        result = await supervisor.run()
        self.assertEqual(result.get("workflow_status"), "SUPERVISOR_TIMEOUT")
        from opsx_cli.supervisor_journal import SupervisorJournal

        journal_path = service.project.runs / "supervisor" / "deadbeef" / "journal.sqlite"
        rows = SupervisorJournal(journal_path).list_entries()
        joined = json.dumps(rows, ensure_ascii=False)
        self.assertIn("STOP", joined)
        self.assertIn("SUPERVISOR_TIMEOUT", joined)
        self.assertIn("why", joined)

    async def test_no_progress_on_repeat_fingerprint(self) -> None:
        from opsx_cli.service import RunService
        from opsx_cli.supervisor import WorkflowSupervisor
        from opsx_cli.supervisor_journal import JournalEntry

        service = RunService(Project.at("."))
        timeouts = SupervisorTimeouts.from_minutes(workflow_timeout_minutes=1, supervisor_timeout_minutes=5, max_attempts=5)
        supervisor = WorkflowSupervisor(service, timeouts=timeouts, change_id="x", run_id="noprog")
        fail_record = {
            "verification_status": "FAIL",
            "exit_code": 1,
            "workflow_status": "FAILED",
            "error": "READ_SCOPE_VIOLATION",
        }
        calls = 0
        committed = JournalEntry(
            entry_id="e1",
            attempt=1,
            fingerprint="",
            classification={},
            status="FIXED",
            created_at="",
            commit_status="COMMITTED",
        )

        async def attempt(_: int) -> tuple[dict, str | None]:
            nonlocal calls
            calls += 1
            return fail_record, None

        async def fake_fix(attempt: int, classification: object) -> JournalEntry:
            supervisor._fixed_progress_keys.add(supervisor._progress_key(classification))
            return committed

        qual = qualify_failure(fail_record, log_tail="READ_SCOPE_VIOLATION")

        with patch.object(supervisor, "_workflow_attempt", side_effect=attempt):
            with patch.object(supervisor, "_run_qualify_pipeline", AsyncMock(return_value=qual)):
                with patch.object(supervisor, "_run_fix_pipeline", side_effect=fake_fix):
                    result = await supervisor.run()
        self.assertEqual(result.get("supervisor_stop"), "NO_PROGRESS")
        self.assertEqual(calls, 2)

    async def test_fix_rounds_retry_rejected_then_commit(self) -> None:
        from opsx_cli.service import RunService
        from opsx_cli.supervisor import WorkflowSupervisor
        from opsx_cli.supervisor_classify import classify_attempt as classify
        from opsx_cli.supervisor_journal import JournalEntry

        service = RunService(Project.at("."))
        timeouts = SupervisorTimeouts.from_minutes(workflow_timeout_minutes=1, supervisor_timeout_minutes=5, max_attempts=3)
        supervisor = WorkflowSupervisor(service, timeouts=timeouts, change_id="x", run_id="rounds")
        cls = classify({"error": "APPLY_BLOCKED"}, log_tail="")
        statuses = ["FIX_REJECTED", "FIX_REJECTED", "FIXED"]
        calls: list[int] = []

        async def fake(attempt: int, classification: object) -> JournalEntry:
            status = statuses[len(calls)]
            calls.append(attempt)
            return JournalEntry(
                entry_id=f"e{len(calls)}", attempt=attempt, fingerprint="", classification={},
                status=status, created_at="", error="r",
                commit_status="COMMITTED" if status == "FIXED" else None,
            )

        with patch.object(supervisor, "_run_fix_pipeline", side_effect=fake):
            entry = await supervisor._run_fix_rounds(1, cls)
        self.assertEqual(entry.status, "FIXED")
        self.assertEqual(len(calls), 3)


class SupervisorPanelJournalExportTests(unittest.TestCase):
    def test_panel_serves_journal_download(self) -> None:
        import secrets

        from fastapi.testclient import TestClient

        from opsx_cli.context import Project
        from opsx_cli.panel import create_app

        state = {
            "token": secrets.token_hex(16),
            "url": "http://127.0.0.1:8765",
            "instance_id": "test",
            "active_run_id": None,
        }
        client = TestClient(create_app(Project.at("."), state), base_url="http://127.0.0.1:8765")
        response = client.get(
            "/api/journal-export",
            headers={"X-OPSX-Token": state["token"]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("content-type", "").split(";")[0], "application/zip")
        disposition = response.headers.get("content-disposition", "")
        self.assertIn("attachment", disposition)
        self.assertIn("supervisor-journal-export.zip", disposition)
        self.assertTrue(response.content[:2] == b"PK", "journal export must be a zip archive")
        import io
        import zipfile

        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = archive.namelist()
        self.assertEqual(names, ["supervisor-journal-export.sqlite"])


class SupervisorJournalExportTests(unittest.TestCase):
    def test_export_does_not_truncate_live_journal(self) -> None:
        import tempfile

        from opsx_cli.supervisor_journal import (
            SupervisorJournal,
            export_supervisor_journal,
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            supervisor = root / "opsx" / "runs" / "supervisor" / "run_a"
            supervisor.mkdir(parents=True)
            live = supervisor / "journal.sqlite"
            journal = SupervisorJournal(live)
            journal.append_row({"record": "analysis", "kind": "SOLVE", "what": "hop"})
            before = journal.list_entries()
            export_path = export_supervisor_journal(root, run_id="run_a")
            after = journal.list_entries()
            journal.append_row({"record": "analysis", "kind": "PASS", "what": "after export"})
            self.assertEqual(len(before), 1)
            self.assertEqual(len(after), 1)
            self.assertEqual(len(journal.list_entries()), 2)
            exported = SupervisorJournal(export_path).list_entries()
            self.assertEqual(len(exported), 1)
            self.assertEqual(exported[0].get("kind"), "SOLVE")


class SupervisorRepairHistoryTests(unittest.IsolatedAsyncioTestCase):
    def test_prior_repairs_scoped_by_change_and_code(self) -> None:
        import tempfile

        from opsx_cli.supervisor_journal import JournalEntry, SupervisorJournal

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sup = root / "opsx" / "runs" / "supervisor"
            j1 = SupervisorJournal(sup / "r1" / "journal.sqlite")
            j1.append(JournalEntry(
                entry_id="e1", attempt=1, fingerprint="fp1",
                classification={"code": "APPLY_BLOCKED", "fingerprint": "fp1"},
                status="FIX_REJECTED", created_at="",
                change_id="chg",
                fix_report={
                    "verdict": "FIXED",
                    "repair_agent": "apply_design",
                    "approach": "add receipt field",
                    "files_changed": ["opsx/opsx_runtime/agents/x.py"],
                },
                files_changed=[{"repo": "core", "path": "opsx/opsx_runtime/agents/x.py", "sha256": "a"}],
                error="checks failed",
            ))
            rows = j1.prior_repairs("chg", "APPLY_BLOCKED", "fp1")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["repair_agent"], "apply_design")
            self.assertEqual(j1.prior_repairs("other", "APPLY_BLOCKED", "fp1"), [])

    def test_repeat_repair_detected(self) -> None:
        from opsx_cli.supervisor_journal import detect_repeat_repair, repair_approach_key

        prior = [{
            "repair_agent": "apply_design",
            "files": ["opsx/opsx_runtime/agents/x.py"],
            "approach": "add receipt field",
            "approach_key": repair_approach_key({
                "repair_agent": "apply_design",
                "approach": "add receipt field",
                "files_changed": ["opsx/opsx_runtime/agents/x.py"],
            }),
        }]
        fix = {
            "verdict": "FIXED",
            "solver_action": "REPAIR_AGENT",
            "repair_agent": "apply_design",
            "approach": "add receipt field",
            "files_changed": ["opsx/opsx_runtime/agents/x.py"],
        }
        self.assertTrue(detect_repeat_repair(fix, prior))

    async def test_repeat_repair_rejected_in_fix_pipeline(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import AsyncMock, patch

        from opsx_cli.context import Project
        from opsx_cli.service import RunService
        from opsx_cli.supervisor import WorkflowSupervisor
        from opsx_cli.supervisor_classify import classify_attempt
        from opsx_cli.supervisor_journal import JournalEntry, SupervisorJournal
        from opsx_cli.supervisor_timeouts import SupervisorTimeouts

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "opsx" / "project").mkdir(parents=True)
            (root / "opsx" / "core").mkdir(parents=True)
            sup = root / "opsx" / "runs" / "supervisor" / "seed"
            sup.mkdir(parents=True)
            prior_journal = SupervisorJournal(sup / "journal.sqlite")
            prior_journal.append(JournalEntry(
                entry_id="old", attempt=1, fingerprint="fp",
                classification={"code": "APPLY_BLOCKED", "fingerprint": "fp"},
                status="FIX_REJECTED", created_at="", change_id="chg",
                fix_report={
                    "verdict": "FIXED",
                    "repair_agent": "apply_design",
                    "approach": "same tweak",
                    "files_changed": ["opsx/opsx_runtime/agents/x.py"],
                },
                files_changed=[{"repo": "core", "path": "opsx/opsx_runtime/agents/x.py", "sha256": "a"}],
            ))
            service = RunService(Project.at(root))
            supervisor = WorkflowSupervisor(
                service,
                timeouts=SupervisorTimeouts.from_minutes(
                    workflow_timeout_minutes=1,
                    supervisor_timeout_minutes=5,
                    max_attempts=3,
                ),
                change_id="chg",
                run_id="live",
            )
            cls = classify_attempt({"error": "APPLY_BLOCKED"}, log_tail="")
            same_fix = {
                "verdict": "FIXED",
                "solver_action": "REPAIR_AGENT",
                "repair_agent": "apply_design",
                "approach": "same tweak",
                "files_changed": ["opsx/opsx_runtime/agents/x.py"],
            }
            async def read_json_side_effect(path: object, default: object = None) -> object:
                if "fix_result.json" in str(path):
                    return same_fix
                return default if default is not None else {}

            with patch.object(supervisor, "_remaining", return_value=600.0):
                with patch.object(supervisor, "_run_worker_mode", AsyncMock(return_value=(0, None))):
                    with patch("opsx_cli.supervisor.read_json", AsyncMock(side_effect=read_json_side_effect)):
                        with patch("opsx_cli.supervisor.write_json", AsyncMock()):
                            with patch("opsx_cli.supervisor.git_dirty_fingerprints", return_value={}):
                                entry = await supervisor._run_fix_pipeline(1, cls)
            self.assertEqual(entry.status, "FIX_REJECTED")
            kinds = [r.get("kind") for r in supervisor._journal.list_entries()]
            self.assertIn("REPAIR_REPEAT_REJECTED", kinds)


if __name__ == "__main__":
    unittest.main()