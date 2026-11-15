"""Unit tests for PHASE_IMPL verify agent helpers."""

from __future__ import annotations

from agents.verify.impl_verify.preflight import (
    collapse_stale_evidence_gaps,
    collect_deterministic_preflight,
    derive_referenced_repo_paths,
    optimize_split_apply_completion_payload,
)
from agents.verify.impl_verify.report import format_verify_impl_report
from agents.verify.impl_verify.schemas import VerifyImplAnswer


def test_collect_deterministic_preflight_includes_optimized_split_apply(
    tmp_path, monkeypatch
):
    """Preflight включает split_apply с STALE только в warnings (не blocking)."""
    change_id = "demo-change"
    root = tmp_path / "openspec" / "changes" / change_id
    root.mkdir(parents=True)
    (root / "proposal.md").write_text("# demo", encoding="utf-8")

    stale = [
        {
            "class": "STALE_EVIDENCE",
            "task_id": f"{i}.1",
            "covering_slice_ids": ["SLICE-001"],
            "detail": "covering GREEN is STALE_GREEN",
        }
        for i in range(1, 6)
    ]
    raw_payload = {
        "split_apply": True,
        "change_id": change_id,
        "gaps": stale,
        "blocking": list(stale),
        "marker": "IMPL_GAPS",
        "next_action": {"command": f"/opsx-apply change={change_id}"},
    }

    def _fake_run(_change_id: str, *, repo_root=tmp_path):
        _ = repo_root
        return optimize_split_apply_completion_payload(raw_payload, change_id=change_id)

    monkeypatch.setattr(
        "agents.verify.impl_verify.preflight.run_impl_verify_stale_green_refresh",
        lambda *_a, **_k: {"status": "SKIP", "reason": "test"},
    )
    monkeypatch.setattr(
        "agents.verify.impl_verify.preflight.run_split_apply_completion",
        _fake_run,
    )
    monkeypatch.setattr(
        "agents.verify.impl_verify.preflight.run_config_contract_check",
        lambda **_: {"status": "PASS"},
    )

    preflight = collect_deterministic_preflight(change_id, repo_root=tmp_path)

    assert preflight["change_exists"] is True
    split = preflight["split_apply_completion"]
    assert split["marker"] == "IMPL_OK"
    assert split["blocking"] == []
    assert len(split["gaps"]) == 1
    assert split["gaps"][0].get("collapsed") is True
    assert split["gaps"][0].get("collapsed_count") == 5
    assert any(
        w.get("class") == "STALE_EVIDENCE" for w in (split.get("warnings") or [])
    )


def test_bureaucracy_gaps_do_not_block_marker():
    """BUREAUCRACY уходит в warnings и не держит marker=IMPL_GAPS."""
    payload = optimize_split_apply_completion_payload(
        {
            "gaps": [
                {
                    "class": "BUREAUCRACY",
                    "detail": "missing docstring on public method",
                }
            ],
            "blocking": [
                {
                    "class": "BUREAUCRACY",
                    "detail": "missing docstring on public method",
                }
            ],
            "marker": "IMPL_GAPS",
        },
        change_id="demo-change",
    )
    assert payload["marker"] == "IMPL_OK"
    assert payload["blocking"] == []
    assert payload["warnings"][0]["class"] == "BUREAUCRACY"


def test_collapse_stale_evidence_gaps_single_warning():
    gaps, src = collapse_stale_evidence_gaps(
        [
            {"class": "STALE_EVIDENCE", "task_id": "1.1", "covering_slice_ids": ["SLICE-001"]},
            {"class": "STALE_EVIDENCE", "task_id": "1.2", "covering_slice_ids": ["SLICE-001"]},
        ],
        change_id="invest-yalta-max-funnel",
        next_action={"command": "/opsx-apply change=invest-yalta-max-funnel"},
    )
    assert len(src) == 2
    assert len(gaps) == 1
    assert gaps[0]["collapsed_count"] == 2
    assert gaps[0].get("severity") == "WARN"
    assert "impl_verify auto-runs" in gaps[0].get("note", "")


def test_stale_gaps_do_not_block_marker():
    payload = optimize_split_apply_completion_payload(
        {
            "gaps": [
                {
                    "class": "STALE_EVIDENCE",
                    "task_id": "1.1",
                    "covering_slice_ids": ["SLICE-001"],
                }
            ],
            "blocking": [
                {
                    "class": "STALE_EVIDENCE",
                    "task_id": "1.1",
                    "covering_slice_ids": ["SLICE-001"],
                }
            ],
            "marker": "IMPL_GAPS",
        },
        change_id="demo-change",
    )
    assert payload["marker"] == "IMPL_OK"
    assert payload["blocking"] == []


def test_derive_referenced_repo_paths_from_artifacts_and_specs():
    bundle = {
        "spec_paths": ["openspec/changes/x/specs/foo/spec.md"],
        "artifacts": {
            "design.md": "See `src/stroy/channels/max/client.py` and tests/unit/foo.py",
            "tasks.md": "Touch migrations/versions/0012_max_outbound_deliveries.py",
        },
    }
    paths = derive_referenced_repo_paths("x", bundle)
    assert "src/stroy/channels/max/client.py" in paths
    assert "tests/unit/foo.py" in paths
    assert "migrations/versions/0012_max_outbound_deliveries.py" in paths
    assert "openspec/changes/x/specs/foo/spec.md" in paths


def test_format_report_ends_with_marker():
    answer = VerifyImplAnswer(
        verdict="IMPL_OK",
        behavior_summary="ok",
        architecture_placement="ok",
        tasks_consistency="ok",
        gap_details=[],
        next_command="",
        local_hop_copy_block="Workspace: /tmp\nChange: demo",
    )
    text = format_verify_impl_report(
        change_id="demo",
        answer=answer,
        preflight={"change_exists": True, "artifact_bundle_meta": {"missing": []}},
        marker="✅ IMPL_OK",
    )
    assert text.strip().endswith("✅ IMPL_OK")
    assert "mode=impl" in text
    assert "split_apply" not in text
