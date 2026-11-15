"""Процесс infra_tests без Docker и без продуктовых тестов."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from infra.engine import InfraEnvSnapshot, apply_decision, release_lease
from infra.profile import load_profile
from workflows.stop_errors import InfraTestsStageError
from workflows.verify.infra_tests.agents import InfraTestTriage
from workflows.verify.infra_tests.executor import (
    circuit_open,
    classify_failure,
    confirm_class,
    summarize_junit,
)
from workflows.verify.infra_tests.pipeline import run_infra_tests
from workflows.verify.infra_tests.reuse import NodeReuseRecord, reuse_eligible
from workflows.verify.infra_tests.runbook import (
    KnownIssue,
    load_runbook,
    promote_verified,
    propose_issue,
    save_runbook,
)
from workflows.verify.infra_tests.selection import select_infra_nodes


def test_selection_empty_is_pass_no_scope() -> None:
    chosen = select_infra_nodes()
    assert chosen.status == "PASS_NO_SCOPE"
    assert chosen.node_ids == ()


def test_selection_unions_involved_and_drops_disabled() -> None:
    chosen = select_infra_nodes(
        trace_nodes=("tests/scenarios/x/test_a.py::test_a",),
        slice_nodes=("tests/integration/y/test_b.py::test_b",),
        disabled_nodes=("tests/integration/y/test_b.py::test_b",),
    )
    assert chosen.status == "SELECTED"
    assert chosen.node_ids == ("tests/scenarios/x/test_a.py::test_a",)
    assert "disabled" in chosen.reasons


def test_full_trigger_uses_all_infra_nodes() -> None:
    chosen = select_infra_nodes(
        trace_nodes=("tests/scenarios/x/test_a.py::test_a",),
        all_infra_nodes=("tests/integration/z/test_c.py::test_c",),
        full_triggers_hit=True,
    )
    assert chosen.scope == "full"
    assert chosen.node_ids == ("tests/integration/z/test_c.py::test_c",)


def test_adopt_does_not_mark_started_by_us() -> None:
    lease = apply_decision(
        InfraEnvSnapshot(
            profile_name="windows-docker-desktop",
            docker_ok=True,
            compose_v2=True,
            port_open=True,
            db_comment="stroy-disposable-test-db",
            expected_comment="stroy-disposable-test-db",
        ),
        run_id="r1",
    )
    assert lease.decision == "ADOPT"
    released = release_lease(lease)
    assert released.started_by_us is False
    assert released.stopped is False


def test_foreign_port_is_alt_not_kill() -> None:
    lease = apply_decision(
        InfraEnvSnapshot(
            profile_name="windows-docker-desktop",
            docker_ok=True,
            compose_v2=True,
            port_open=True,
            db_comment="stroy-preview-worker-db",
            expected_comment="stroy-disposable-test-db",
        ),
        run_id="r2",
    )
    assert lease.decision == "START_ALT_PORT"


def test_unavailable_raises_and_releases(tmp_path: Path) -> None:
    _ = tmp_path
    with pytest.raises(InfraTestsStageError) as caught:
        asyncio.run(
            run_infra_tests(
                {
                    "run_uuid": "r3",
                    "infra_trace_nodes": [
                        "tests/integration/example.py::test_needs_db",
                    ],
                }
            )
        )
    assert caught.value.code == "BLOCKED_ENV"


def test_unavailable_without_infra_scope_is_pass_no_scope() -> None:
    """Инфра недоступна, но change не выбрал infra nodes — verify идёт дальше."""
    patch = asyncio.run(run_infra_tests({"run_uuid": "r3b"}))
    assert patch["infra_tests_status"] == "PASS_NO_SCOPE"


def test_pass_no_scope_when_env_can_start() -> None:
    patch = asyncio.run(
        run_infra_tests(
            {
                "run_uuid": "r4",
                "infra_env_snapshot": {
                    "profile_name": "linux-docker",
                    "docker_ok": True,
                    "compose_v2": True,
                    "port_open": False,
                    "db_comment": None,
                    "expected_comment": "stroy-disposable-test-db",
                },
            }
        )
    )
    assert patch["infra_tests_status"] == "PASS_NO_SCOPE"


def test_junit_skip_is_fail() -> None:
    xml = """<?xml version="1.0"?>
    <testsuite>
      <testcase classname="t" name="a"><skipped message="no db"/></testcase>
    </testsuite>"""
    summary = summarize_junit(xml)
    assert summary.status == "FAIL"
    assert summary.owner == "SKIP_AS_PASS"


def test_signature_and_circuit() -> None:
    assert classify_failure("connection refused") == "ENV"
    assert classify_failure("nothing known") == "UNKNOWN"
    assert circuit_open(("ENV:1", "ENV:1", "ENV:1")) is True
    assert confirm_class(failed_in_parallel=True, passed_in_isolation=True) == "ISOLATION_DEFECT"


def test_triage_without_handler_raises() -> None:
    with pytest.raises(InfraTestsStageError) as caught:
        InfraTestTriage().run({"text": "mystery"})
    assert caught.value.code == "INFRA_AGENT_REQUIRED"


def test_runbook_promote_requires_pass(tmp_path: Path) -> None:
    path = tmp_path / "known-issues.yaml"
    doc = propose_issue(
        load_runbook(path),
        KnownIssue(id="port-busy", signature="port", symptom="busy", remedy="alt port", status="proposed"),
    )
    saved = save_runbook(path, doc, expected_revision=0)
    with pytest.raises(ValueError):
        promote_verified(saved, "port-busy", passed=False, evidence="no")
    promoted = promote_verified(saved, "port-busy", passed=True, evidence="probe ok")
    assert promoted.issues[0].status == "verified"


def test_profile_loads() -> None:
    repo = Path(__file__).resolve().parents[6]
    profile = load_profile(repo / "opsx" / "project" / "infra" / "infra-profile.yaml")
    assert profile.services[0].host_port == 55432
    assert profile.run.parallel_qualified is False


def test_reuse_requires_both_digests() -> None:
    record = NodeReuseRecord("n", "a", "b", "PASS")
    assert reuse_eligible(record, file_digest="a", closure_digest="b") is True
    assert reuse_eligible(record, file_digest="a", closure_digest="c") is False
