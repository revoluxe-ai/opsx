"""Focused contract tests for the Sherlock append-only ledger helper."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
HELPER = (
    Path(__file__).resolve().parents[1]
    / "sherlock"
    / "sherlock_ledger.py"
)
CASE_ID = "SHK-20260926T190000Z"


def _run_event(
    repo_root: Path, payload: dict[str, object], *, expect_ok: bool = True
) -> subprocess.CompletedProcess[str]:
    """Append one event through the public CLI and assert expected success."""

    event_file = repo_root / "event.json"
    _ = event_file.write_text(
        json.dumps(payload, ensure_ascii=True), encoding="utf-8"
    )
    result = subprocess.run(
        [
            sys.executable,
            str(HELPER),
            "append",
            "--repo-root",
            str(repo_root),
            "--sherlock-case",
            CASE_ID,
            "--event-file",
            str(event_file),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert (result.returncode == 0) is expect_ok, result.stderr
    return result


def _start(repo_root: Path) -> None:
    """Create an initialized investigation ledger."""

    _ = _run_event(
        repo_root,
        {
            "EVENT_TYPE": "INVESTIGATION_STARTED",
            "SHERLOCK_CASE_ID": CASE_ID,
            "SYMPTOM": "A scheduled operation produced an unexplained result.",
            "LINKED_BUG_CASE": "BUGCASE-EXAMPLE",
            "LINKED_FINDING": "ROOT-001",
            "SYSTEM_STATE_REF": "commit:abc123",
        },
    )


def _record_fact_and_hypotheses(repo_root: Path) -> None:
    """Record one citable fact and two competing hypotheses."""

    _ = _run_event(
        repo_root,
        {
            "EVENT_TYPE": "FACT_RECORDED",
            "FACT_ID": "FACT-001",
            "STATEMENT": "The focused log records timeout X.",
            "EVIDENCE_KIND": "durable_artifact",
            "EVIDENCE_REF": ".tmp/ci/focused.log:12",
            "OBSERVED_AT": "2026-09-26T19:00:00Z",
            "SYSTEM_STATE_REF": "commit:abc123",
        },
    )
    for hypothesis_id, statement in (
        ("HYP-001", "Timeout X caused the symptom."),
        ("HYP-002", "A stale worker caused the symptom."),
    ):
        _ = _run_event(
            repo_root,
            {
                "EVENT_TYPE": "HYPOTHESIS_RECORDED",
                "HYPOTHESIS_ID": hypothesis_id,
                "STATEMENT": statement,
            },
        )


def test_no_evidence_cannot_create_root_cause(tmp_path: Path) -> None:
    """A root-cause event without facts or a supported hypothesis is rejected."""

    _start(tmp_path)
    result = _run_event(
        tmp_path,
        {
            "EVENT_TYPE": "CONCLUSION_RECORDED",
            "ROOT_CAUSE": "An unsupported guess.",
            "SUPPORTED_BY": [],
        },
        expect_ok=False,
    )
    assert "exactly one SUPPORTED hypothesis" in result.stderr
    ledger = (
        tmp_path
        / "opsx"
        / "runs"
        / "sherlock"
        / CASE_ID
        / "LEDGER.jsonl"
    )
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1


def test_append_only_cause_gate_and_projection(tmp_path: Path) -> None:
    """A cause is projected only after one supported and one refuted branch."""

    _start(tmp_path)
    _record_fact_and_hypotheses(tmp_path)
    _ = _run_event(
        tmp_path,
        {
            "EVENT_TYPE": "HYPOTHESIS_STATUS_CHANGED",
            "HYPOTHESIS_ID": "HYP-001",
            "STATUS": "SUPPORTED",
            "SUPPORTED_BY": ["FACT-001"],
        },
    )
    case_dir = tmp_path / "opsx" / "runs" / "sherlock" / CASE_ID
    ledger = case_dir / "LEDGER.jsonl"
    prefix_before_rejection = ledger.read_bytes()
    rejected = _run_event(
        tmp_path,
        {
            "EVENT_TYPE": "CONCLUSION_RECORDED",
            "ROOT_CAUSE": "Timeout X.",
            "SUPPORTED_BY": ["FACT-001", "HYP-001"],
        },
        expect_ok=False,
    )
    assert "no OPEN siblings" in rejected.stderr
    assert ledger.read_bytes() == prefix_before_rejection

    _ = _run_event(
        tmp_path,
        {
            "EVENT_TYPE": "HYPOTHESIS_STATUS_CHANGED",
            "HYPOTHESIS_ID": "HYP-002",
            "STATUS": "REFUTED",
            "CONTRADICTED_BY": ["FACT-001"],
        },
    )
    prefix_before_conclusion = ledger.read_bytes()
    _ = _run_event(
        tmp_path,
        {
            "EVENT_TYPE": "CONCLUSION_RECORDED",
            "ROOT_CAUSE": "Timeout X caused the observed result.",
            "SUPPORTED_BY": ["FACT-001", "HYP-001"],
        },
    )
    assert ledger.read_bytes().startswith(prefix_before_conclusion)
    projection = (case_dir / "INVESTIGATION.md").read_text(encoding="utf-8")
    assert "STATUS: CAUSE_SUPPORTED" in projection
    assert "root_cause: Timeout X caused the observed result." in projection
    assert "GENERATED_FROM: LEDGER.jsonl" in projection


def test_acquisition_rejects_raw_and_stale_fulfillment(tmp_path: Path) -> None:
    """Acquisition accepts only allowlisted summaries and matching state refs."""

    _start(tmp_path)
    _record_fact_and_hypotheses(tmp_path)
    _ = _run_event(
        tmp_path,
        {
            "EVENT_TYPE": "EVIDENCE_GAP_RECORDED",
            "GAP_ID": "GAP-001",
            "QUESTION": "Which worker revision handled the operation?",
            "BLOCKS": ["HYP-001", "HYP-002"],
        },
    )
    raw_result = _run_event(
        tmp_path,
        {
            "EVENT_TYPE": "ACQUISITION_REQUESTED",
            "REQUEST_ID": "REQ-RAW",
            "EVIDENCE_GAP": "GAP-001",
            "SOURCE": "opsx-devops",
            "REQUIRED_AUTHORITY": "opsx-devops",
            "ACCESS_MODE": "raw_dump",
            "REDACTION_CONSTRAINTS": "No secrets.",
            "REQUESTED_AT": "2026-09-26T19:01:00Z",
            "SYSTEM_STATE_REF_AT_REQUEST": "deploy:rev-1",
        },
        expect_ok=False,
    )
    assert "least-privilege allowlisted" in raw_result.stderr

    _ = _run_event(
        tmp_path,
        {
            "EVENT_TYPE": "ACQUISITION_REQUESTED",
            "REQUEST_ID": "REQ-001",
            "EVIDENCE_GAP": "GAP-001",
            "SOURCE": "opsx-devops",
            "REQUIRED_AUTHORITY": "opsx-devops",
            "ACCESS_MODE": "revision_id_only",
            "REDACTION_CONSTRAINTS": "No logs, payloads, or secrets.",
            "REQUESTED_AT": "2026-09-26T19:01:00Z",
            "SYSTEM_STATE_REF_AT_REQUEST": "deploy:rev-1",
        },
    )
    stale_result = _run_event(
        tmp_path,
        {
            "EVENT_TYPE": "ACQUISITION_STATUS_CHANGED",
            "REQUEST_ID": "REQ-001",
            "STATUS": "FULFILLED",
            "SYSTEM_STATE_REF_RETURNED": "deploy:rev-2",
            "EVIDENCE_FACT_IDS": ["FACT-001"],
        },
        expect_ok=False,
    )
    assert "STALE_SUSPECTED" in stale_result.stderr
    _ = _run_event(
        tmp_path,
        {
            "EVENT_TYPE": "ACQUISITION_STATUS_CHANGED",
            "REQUEST_ID": "REQ-001",
            "STATUS": "STALE_SUSPECTED",
            "SYSTEM_STATE_REF_RETURNED": "deploy:rev-2",
            "REASON": "Deployment revision changed before evidence returned.",
        },
    )


def test_target_command_and_registry_contract() -> None:
    """The transferred command is target-native and registry hashes are current."""

    command_path = ROOT / ".cursor" / "commands" / "opsx-sherlock.md"
    command_body = command_path.read_text(encoding="utf-8")
    required_tokens = (
        "contract_id: OPSX-WB-SHERLOCK-01",
        "# /opsx-sherlock",
        "$ARGUMENTS",
        "NO EVIDENCE -> NO ROOT-CAUSE CLAIM",
        "FACT_RECORDED",
        "HYPOTHESIS_RECORDED",
        "LEDGER.jsonl",
        "INVESTIGATION.md",
        "opsx-devops INVESTIGATE",
        "IMPLEMENTED_UNVERIFIED",
        "CLASSIFICATION_AUTHORITY: NO",
        "PRODUCTION_READ_AUTHORITY: NO",
    )
    for token in required_tokens:
        assert token in command_body
    forbidden_tokens = (
        "soft_prod_server_ops",
        "opsx/adapters/vue-npm",
        "host=<declared host>",
        "STATUS: GREEN",
        "CHOSEN_ROUTE:",
    )
    for token in forbidden_tokens:
        assert token not in command_body

    command_hash = hashlib.sha256(command_path.read_bytes()).hexdigest()
    activation_path = ROOT / "opsx" / "registry" / "command_activation.ndjson"
    activation_rows = [
        json.loads(line)
        for line in activation_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    activation = [
        row for row in activation_rows if row.get("command_id") == "opsx-sherlock"
    ]
    assert len(activation) == 1
    assert activation[0]["command_hash"] == command_hash
    assert activation[0]["runtime_class"] == "ACTIVE_WITH_LIMITS"

    provenance_path = ROOT / "opsx" / "registry" / "provenance.ndjson"
    provenance_rows = [
        json.loads(line)
        for line in provenance_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    events = [
        row
        for row in provenance_rows
        if row.get("event") == "full_command_transfer_batch"
        and row.get("commands") == ["opsx-sherlock"]
    ]
    assert len(events) == 1
    assert events[0]["command_hashes"] == [command_hash]
    assert (
        events[0]["source_commit"]
        == "91311ada29721cfb9aab7a00e1dc4d2a0a8d1982"
    )

    manifest = (
        ROOT / "opsx" / "registry" / "command_transfer_manifest.md"
    ).read_text(encoding="utf-8")
    expected_row = "| `opsx-sherlock` | `FUNCTIONAL_PROVEN` |"
    assert expected_row in manifest

    proof_path = (
        ROOT / "opsx" / "registry" / "active_command_activation_proof.json"
    )
    proof = json.loads(proof_path.read_text(encoding="utf-8"))
    matching = [
        row for row in proof["commands"] if row.get("command") == "opsx-sherlock"
    ]
    assert len(matching) == 1
    assert matching[0]["proof_pass"] is True
    assert matching[0]["command_sha256"] == command_hash
