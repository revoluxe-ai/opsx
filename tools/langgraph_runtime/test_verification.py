"""Contract tests for the real LangGraph verification runtime (fake I/O)."""
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from tools.langgraph_runtime.journal import DispatchJournal
from tools.langgraph_runtime.verification import build_verification_graph


def required(id="check-a", rev="r1"):
    return {
        "completed": True,
        "status": "CHECK_REQUIRED",
        "current_required_check_id": id,
        "plan_id": "p1",
        "assignment_epoch": 1,
        "progress_pass": 0,
        "progress_total": 1,
        "raw_output": "STATUS: CHECK_REQUIRED",
        "execute_assignment": {
            "bounded_verify_target": True,
            "change_id": "test-change",
            "plan_id": "p1",
            "required_check_id": id,
            "assignment_epoch": 1,
            "ledger_revision": rev,
            "executor": "bounded-check",
            "expected_result_path": "evidence/result.json",
        },
    }


def passed():
    return {
        "completed": True,
        "status": "ALL_REQUIRED_CHECKS_PASS",
        "progress_pass": 1,
        "progress_total": 1,
        "plan_id": "p1",
        "raw_output": "STATUS: ALL_REQUIRED_CHECKS_PASS",
    }


def verified():
    return {
        "completed": True,
        "uncertain": False,
        "durable_result_found": True,
        "durable_result_status": "PASS",
        "required_check_id": "check-a",
        "expected_result_path": "evidence/result.json",
        "executor": "bounded-check",
        "raw_output": "PASS",
    }


class FakeBackend:
    def __init__(self, checks, outputs=(), ctx=None, durable=None):
        self.checks = list(checks)
        self.outputs = list(outputs)
        self.ctx = ctx or {"status": "OK"}
        self.durable = durable
        self.executed = 0

    def ensure_context(self, change):
        return self.ctx

    def next_check(self, change):
        return self.checks.pop(0)

    def execute_assigned(self, change, assignment):
        self.executed += 1
        return self.outputs.pop(0)

    def inspect_result(self, assignment):
        return self.durable

    def recovered_result(self, assignment, durable):
        return verified()


def run_case(tmp_path: Path, backend: FakeBackend, run_id="verification:test"):
    with SqliteSaver.from_conn_string(str(tmp_path / "checkpoints.sqlite3")) as saver:
        saver.setup()
        journal = DispatchJournal(tmp_path / "dispatch.sqlite3")
        graph = build_verification_graph(
            backend, journal, repo=tmp_path, checkpointer=saver
        )
        return graph.invoke(
            {"run_id": run_id, "change": "test-change"},
            config={"configurable": {"thread_id": run_id}},
            durability="sync",
        )


def test_real_contract_full_pass(tmp_path):
    backend = FakeBackend([required(), passed()], [verified()])
    state = run_case(tmp_path, backend)
    assert state["status"] == "ALL_REQUIRED_CHECKS_PASS"
    assert state["next_check_runs"] == 2
    assert state["executor_runs"] == 1
    assert backend.executed == 1


def test_preflight_blocks(tmp_path):
    backend = FakeBackend([], ctx={"status": "BLOCKED", "block_reason": "PLAN_MISSING"})
    state = run_case(tmp_path, backend)
    assert state["status"] == "BLOCKED"
    assert backend.executed == 0


def test_uncertain_no_reexecution(tmp_path):
    backend = FakeBackend([required()], [{
        "completed": False,
        "uncertain": True,
        "durable_result_found": False,
        "required_check_id": "check-a",
        "raw_output": "cursor detached",
    }])
    state = run_case(tmp_path, backend)
    assert state["status"] == "EXECUTOR_COMPLETION_UNCERTAIN"
    assert backend.executed == 1


def test_unchanged_assignment_pauses_before_second_dispatch(tmp_path):
    backend = FakeBackend([required(), required()], [verified()])
    state = run_case(tmp_path, backend)
    assert state["status"] == "VERIFICATION_NON_CONVERGENT_PAUSE"
    assert backend.executed == 1


def test_journal_prevents_replaying_inflight_side_effect(tmp_path):
    fake = FakeBackend([], [verified()])
    journal = DispatchJournal(tmp_path / "dispatch.sqlite3")
    assignment = required()["execute_assignment"]
    journal.claim("id", "test-change", assignment)
    outcome = journal.execute(fake, "id", "test-change", assignment)
    assert outcome["uncertain"] is True
    assert fake.executed == 0


def test_journal_reconciles_durable_evidence(tmp_path):
    fake = FakeBackend([], [verified()], durable={"STATUS": "PASS"})
    journal = DispatchJournal(tmp_path / "dispatch.sqlite3")
    assignment = required()["execute_assignment"]
    journal.claim("id", "test-change", assignment)
    outcome = journal.execute(fake, "id", "test-change", assignment)
    assert outcome["durable_result_found"] is True
    assert fake.executed == 0


def test_journal_idempotent_completed_step(tmp_path):
    fake = FakeBackend([], [verified()])
    journal = DispatchJournal(tmp_path / "dispatch.sqlite3")
    assignment = required()["execute_assignment"]
    journal.execute(fake, "id", "test-change", assignment)
    outcome = journal.execute(fake, "id", "test-change", assignment)
    assert outcome["durable_result_found"] is True
    assert fake.executed == 1
