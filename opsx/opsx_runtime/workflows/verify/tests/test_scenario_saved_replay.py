"""Replay-salvage authority tests: exact provenance, preflight, no false PASS."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.verify.scenario_coverage_verify import replay_saved

CHANGE = "demo"
RUN = "historical"
ANCHOR = "a" * 40
ROOT_PREFIX = "opsx/receipts/demo/"
SOURCE = ROOT_PREFIX + "scenario-row-streams/historical/"
CANDIDATES = ROOT_PREFIX + "scenario-recovery-historical/"


def _make(root, monkeypatch, count=2):
    index_path = root / CANDIDATES / "INDEX.json"
    index_path.parent.mkdir(parents=True)
    meta_dir = root / "opsx/opsx_runtime/run_dialogs/demo/historical/scenario_coverage_verify"
    meta_dir.mkdir(parents=True)
    records = []
    for n in range(count):
        sid = f"scenario-{n}"
        key = f"key-{n}"
        source = SOURCE + key + ".conversation.json"
        candidate = CANDIDATES + "rows/" + key + ".json"
        answer = {
            "verdict": "SCENARIO_TEST_COVERAGE_FAIL" if n else "SCENARIO_TEST_COVERAGE_PASS",
            "plan_evidence_id": "frozen", "source_digest": "f" * 64,
            "row_reviews": [{"scenario_id": sid, "assessment": "GAP" if n else "SUPPORTED"}],
            "gaps": [{"scenario_id": sid, "gap_class": "NO_ASSERT", "evidence": "missing"}] if n else [],
        }
        src_file = root / source
        src_file.parent.mkdir(parents=True, exist_ok=True)
        src_file.write_text(json.dumps([{"turn": {"steps": [
            {"type": "assistantMessage", "message": {"text": json.dumps(answer)}},
        ]}}]), encoding="utf-8")
        cfile = root / candidate
        cfile.parent.mkdir(parents=True, exist_ok=True)
        cfile.write_text(json.dumps({
            "contract": replay_saved.CANDIDATE_CONTRACT, "gate_authority": "NONE",
            "source_revision_at_capture": ANCHOR, "change_id": CHANGE,
            "execution_id": RUN, "conversation_path": source,
            "original_conversation_git_blob_sha": "trusted-blob",
            "full_sdk_answer": answer,
        }), encoding="utf-8")
        md = (meta_dir / "meta.json") if n == 0 else (
            meta_dir / "revisions" / f"run-{n}" / "meta.json"
        )
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(json.dumps({
            "legacy_conversation_json": f"/original/checkout/{source}",
            "sdk_status": "finished", "sdk_run_id": f"run-{n}",
            "agent_id": f"agent-{n}", "model_id": "composer-2.5",
            "agent": "scenario_coverage_verify", "run_uuid": RUN,
            "change_id": CHANGE,
        }), encoding="utf-8")
        records.append({"source": source, "candidate": candidate})
    index_path.write_text(json.dumps({
        "contract": replay_saved.INDEX_CONTRACT, "gate_authority": "NONE",
        "change_id": CHANGE, "execution_id": RUN,
        "captured_from_git_commit": ANCHOR,
        "rows_completed": count, "records": records,
    }), encoding="utf-8")
    monkeypatch.setattr(replay_saved, "_git", lambda *args: b"trusted-blob\n")
    monkeypatch.setattr(replay_saved, "input_snapshot",
                        lambda *args: {"source_digest": "f" * 64})
    calls = []

    def native_recover(**kwargs):
        calls.append(kwargs)
        answer = replay_saved._final_response(kwargs["conversation_path"])
        status = answer["row_reviews"][0]["assessment"]
        write = kwargs["write"]
        return {
            "counts": {
                "SUPPORTED": 1 if status == "SUPPORTED" else 0,
                "GAP": 1 if status == "GAP" else 0,
                "INSPECTION_BLOCKED": 0, "CONTRACT_CONFLICT": 0,
            },
            "eligible_supported_receipts": int(status == "SUPPORTED"),
            "rejected_supported_proofs": {},
            "written": {
                "observations": int(write),
                "supported_receipts": int(write and status == "SUPPORTED"),
            },
        }
    monkeypatch.setattr(replay_saved, "recover", native_recover)
    return index_path, calls


def _run(root, index, *, write=False):
    return replay_saved.replay_saved(
        root=root, change_id=CHANGE, index_path=index, write=write,
    )


def test_dry_run_preflights_all_rows_without_writing(tmp_path, monkeypatch):
    index, calls = _make(tmp_path, monkeypatch)
    report = _run(tmp_path, index)
    assert report["status"] == "DRY_RUN"
    assert report["sdk_calls"] == 0
    assert report["aggregate_verdict"] == "NOT_ASSERTED"
    assert report["requested_rows"] == 2
    assert report["eligible_supported_receipts"] == 1
    assert report["counts"]["GAP"] == 1
    assert report["written"] == {"observations": 0, "supported_receipts": 0}
    assert len(calls) == 2 and all(not x["write"] for x in calls)


def test_explicit_write_occurs_only_after_complete_preflight(tmp_path, monkeypatch):
    index, calls = _make(tmp_path, monkeypatch)
    report = _run(tmp_path, index, write=True)
    assert report["status"] == "RECOVERED"
    assert [x["write"] for x in calls] == [False, False, True, True]
    assert report["written"] == {"observations": 2, "supported_receipts": 1}
    assert report["aggregate_verdict"] == "NOT_ASSERTED"
    assert all(x["anchor"] == ANCHOR for x in calls)


@pytest.mark.parametrize("field,value", [
    ("contract", "unsupported-index"), ("gate_authority", "PASS"),
    ("change_id", "another-change"), ("execution_id", "../escape"),
    ("captured_from_git_commit", "short"), ("rows_completed", 12),
    ("records", []),
])
def test_index_identity_tamper_fails_before_native_writes(tmp_path, monkeypatch, field, value):
    index, calls = _make(tmp_path, monkeypatch)
    data = json.loads(index.read_text(encoding="utf-8"))
    data[field] = value
    index.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="REPLAY_"):
        _run(tmp_path, index, write=True)
    assert not calls


@pytest.mark.parametrize("field,value", [
    ("contract", "candidate-v99"), ("gate_authority", "PASS"),
    ("execution_id", "other"), ("change_id", "other"),
    ("source_revision_at_capture", "b" * 40),
    ("original_conversation_git_blob_sha", "not-the-trusted-sha"),
    ("conversation_path", "openspec/changes/other/other.json"),
    ("full_sdk_answer", {"row_reviews": []}),
])
def test_candidate_provenance_tamper_fails_closed(tmp_path, monkeypatch, field, value):
    index, calls = _make(tmp_path, monkeypatch)
    data = json.loads(index.read_text(encoding="utf-8"))
    candidate_path = tmp_path / data["records"][0]["candidate"]
    obj = json.loads(candidate_path.read_text(encoding="utf-8"))
    obj[field] = value
    candidate_path.write_text(json.dumps(obj), encoding="utf-8")
    with pytest.raises(ValueError, match="REPLAY_"):
        _run(tmp_path, index, write=True)
    assert not any(x["write"] for x in calls)


def test_duplicate_scenario_and_duplicate_file_never_replayed(tmp_path, monkeypatch):
    index, calls = _make(tmp_path, monkeypatch)
    data = json.loads(index.read_text(encoding="utf-8"))
    data["records"][1] = dict(data["records"][0])
    index.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="REPLAY_INDEX_ROWS_INVALID"):
        _run(tmp_path, index, write=True)
    assert not calls


def test_raw_original_dialogue_tamper_fails_before_write(tmp_path, monkeypatch):
    index, calls = _make(tmp_path, monkeypatch)
    path = tmp_path / SOURCE / "key-0.conversation.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="(?:REPLAY|RECOVERY)_"):
        _run(tmp_path, index, write=True)
    assert not any(x["write"] for x in calls)


def test_original_not_at_pinned_git_anchor_fails_closed(tmp_path, monkeypatch):
    index, calls = _make(tmp_path, monkeypatch)
    monkeypatch.setattr(replay_saved, "_git", lambda root, *args: (
        b"changed-git-sha\n" if args[0] == "rev-parse" else b"trusted-blob\n"
    ))
    with pytest.raises(ValueError, match="REPLAY_ORIGINAL_BLOB_CHANGED"):
        _run(tmp_path, index, write=True)
    assert not any(x["write"] for x in calls)


def test_terminal_meta_not_at_pinned_git_anchor_fails_closed(tmp_path, monkeypatch):
    index, calls = _make(tmp_path, monkeypatch)
    monkeypatch.setattr(replay_saved, "_git", lambda root, *args: (
        b"changed-git-sha\n"
        if args[0] == "rev-parse" and args[1].endswith("meta.json")
        else b"trusted-blob\n"
    ))
    with pytest.raises(ValueError, match="REPLAY_METADATA_BLOB_CHANGED"):
        _run(tmp_path, index, write=True)
    assert not any(x["write"] for x in calls)


def test_missing_or_ambiguous_terminal_metadata_fails_closed(tmp_path, monkeypatch):
    index, calls = _make(tmp_path, monkeypatch)
    meta = tmp_path / "opsx/opsx_runtime/run_dialogs/demo/historical/scenario_coverage_verify/meta.json"
    meta.unlink()
    with pytest.raises(ValueError, match="REPLAY_METADATA_NOT_UNIQUE"):
        _run(tmp_path, index, write=True)
    assert not any(x["write"] for x in calls)


def test_preflight_failure_for_sibling_prevents_all_writes(tmp_path, monkeypatch):
    index, calls = _make(tmp_path, monkeypatch)
    original = replay_saved.recover

    def fail_second(**kwargs):
        if kwargs["conversation_path"].name.startswith("key-1"):
            raise ValueError("RECOVERY_SINGLE_ROW_INVALID")
        return original(**kwargs)

    monkeypatch.setattr(replay_saved, "recover", fail_second)
    with pytest.raises(ValueError, match="RECOVERY_SINGLE_ROW_INVALID"):
        _run(tmp_path, index, write=True)
    assert calls and all(not x["write"] for x in calls)


def test_post_preflight_write_failure_is_explicit_partial_pause(tmp_path, monkeypatch):
    index, calls = _make(tmp_path, monkeypatch)
    original = replay_saved.recover

    def fail_second_write(**kwargs):
        if kwargs["write"] and kwargs["conversation_path"].name.startswith("key-1"):
            raise OSError("disk-full")
        return original(**kwargs)

    monkeypatch.setattr(replay_saved, "recover", fail_second_write)
    report = _run(tmp_path, index, write=True)
    assert report["status"] == "PARTIAL_RECOVERY_PAUSE"
    assert report["completed_rows"] == 1
    assert report["requested_rows"] == 2
    assert "disk-full" in report["failure"]
    assert report["aggregate_verdict"] == "NOT_ASSERTED"


def test_symlinked_candidate_is_rejected(tmp_path, monkeypatch):
    index, calls = _make(tmp_path, monkeypatch)
    data = json.loads(index.read_text(encoding="utf-8"))
    candidate_path = tmp_path / data["records"][0]["candidate"]
    contents = candidate_path.read_text(encoding="utf-8")
    candidate_path.unlink()
    replacement = tmp_path / "outside-candidate.json"
    replacement.write_text(contents, encoding="utf-8")
    candidate_path.symlink_to(replacement)
    with pytest.raises(ValueError, match="REPLAY_PATH_UNSAFE"):
        _run(tmp_path, index, write=True)
    assert not any(x["write"] for x in calls)


def test_source_changed_mid_preflight_is_rejected(tmp_path, monkeypatch):
    index, calls = _make(tmp_path, monkeypatch)
    reads = iter(["f" * 64, "a" * 64])
    monkeypatch.setattr(replay_saved, "input_snapshot",
                        lambda *args: {"source_digest": next(reads)})
    with pytest.raises(ValueError, match="REPLAY_SOURCE_CHANGED_DURING_PREFLIGHT"):
        _run(tmp_path, index, write=True)
    assert len(calls) == 2 and all(not x["write"] for x in calls)
