"""Hermetic T1–T15: PBH hunt review contract recovery (parser + loop metadata).

Фикстуры — structural reproduction Owner incidents; verbatim ledger bytes
для invest-yalta @ 66222d37 на cloud VM отсутствуют (см. Phase 5 report).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cursor_adapter import (
    MockCursorAdapter,
    MockScenario,
    MockScenarioStep,
    _mock_hunt_repair_step,
    build_hunt_orch_clear_output,
    build_hunt_clean_output,
)
from lanes import PROJECT_BUG_HUNT_LANE, make_project_bug_hunt_lane
from loop_engine import ReviewRepairLoop
from models import REVIEW_CONTRACT_INVALID, REVIEW_UNPARSEABLE, raw_output_sha256
from parsers import parse_project_bug_hunt_result

_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "project_bug_hunt"
_DUAL_REPR = _FIXTURE_DIR / "external_client_notifications_pilot_dual_repr_hunt_complete.txt"
_INVEST_YALTA_TAIL = (
    _FIXTURE_DIR / "invest_yalta_max_funnel_duplicate_findings_tail_hunt_complete.txt"
)
_INCIDENT_SHA256 = (
    "57897019f696d5c4e134ff912745f7875fd3b729307712c0f1bbf64c1c90909d"
)


def _hunt_loop(raw: str) -> tuple[ReviewRepairLoop, MockCursorAdapter]:
    adapter = MockCursorAdapter(
        MockScenario(
            name="pbh-contract-recovery",
            steps=[MockScenarioStep(PROJECT_BUG_HUNT_LANE.review_prompt, raw)],
        )
    )
    return ReviewRepairLoop(adapter, lane=PROJECT_BUG_HUNT_LANE, mock_mode=True), adapter


def test_t1_dual_representation_canonical_plus_markdown_completes():
    parsed = parse_project_bug_hunt_result(_DUAL_REPR.read_text(encoding="utf-8"))
    assert parsed.completed is True
    assert parsed.parse_error_code is None
    assert len(parsed.findings) == 4


def test_t2_invest_yalta_duplicate_findings_tail_completes():
    raw = _INVEST_YALTA_TAIL.read_text(encoding="utf-8")
    parsed = parse_project_bug_hunt_result(raw)
    assert parsed.completed is True
    assert parsed.clean is False  # repairable tests/ paths
    assert parsed.parse_error_code is None
    assert [row["finding_id"] for row in parsed.findings] == ["PBH-001", "PBH-002"]


def test_t3_conflicting_duplicate_canonical_block_is_invalid():
    raw = """STATUS: HUNT_COMPLETE
- findings_total: 1

### FINDING_ID: PBH-001
- LOC_LINK: src/a.py:1

### FINDING_ID: PBH-001
- LOC_LINK: src/b.py:2
"""
    parsed = parse_project_bug_hunt_result(raw)
    assert parsed.parse_error_code == REVIEW_CONTRACT_INVALID
    assert any("conflicts with earlier" in err for err in parsed.parse_errors)


def test_t4_pre_fix_invest_yalta_tail_would_have_failed_without_dedupe():
    raw = _INVEST_YALTA_TAIL.read_text(encoding="utf-8")
    from parsers import normalize_hunt_output, validate_hunt_output

    output = normalize_hunt_output(raw)
    # Симуляция pre-fix: без dedupe canonical дал бы 4 блока.
    from parsers import _extract_canonical_hunt_findings, _strip_hunt_markdown

    canonical_only = _extract_canonical_hunt_findings(_strip_hunt_markdown(raw))
    assert len(canonical_only) == 4
    assert validate_hunt_output(output)[0] is None


def test_t5_findings_total_mismatch_still_invalid():
    raw = """STATUS: HUNT_COMPLETE
- findings_total: 3

### FINDING_ID: PBH-001
- LOC_LINK: src/a.py:1
"""
    parsed = parse_project_bug_hunt_result(raw)
    assert parsed.parse_error_code == REVIEW_CONTRACT_INVALID
    assert "findings_total=3" in parsed.parse_errors[0]


def test_t6_review_contract_invalid_blocks_repair_rounds():
    raw = """STATUS: HUNT_COMPLETE
- findings_total: 2

### FINDING_ID: PBH-001
- LOC_LINK: src/a.py:1
"""
    loop, adapter = _hunt_loop(raw)
    outcome = loop.run()
    assert outcome.status == REVIEW_CONTRACT_INVALID
    assert outcome.repair_rounds == 0
    assert len(adapter.history) == 1


def test_t7_duplicate_tail_fixture_sha256_documented_gap():
    raw = _INVEST_YALTA_TAIL.read_text(encoding="utf-8")
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert digest != _INCIDENT_SHA256


def test_t8_narrative_only_is_unparseable():
    assert (
        parse_project_bug_hunt_result("Looks fine to me.").parse_error_code
        == REVIEW_UNPARSEABLE
    )


def test_t9_invest_yalta_tail_reaches_repair_in_mock_loop():
    raw = _INVEST_YALTA_TAIL.read_text(encoding="utf-8")
    hunt = make_project_bug_hunt_lane(change="invest-yalta-max-funnel")
    adapter = MockCursorAdapter(
        MockScenario(
            name="invest-yalta-tail-repair",
            steps=[
                MockScenarioStep(hunt.review_prompt, raw),
                MockScenarioStep(hunt.repair_prompt, build_hunt_orch_clear_output()),
                MockScenarioStep(hunt.review_prompt, build_hunt_clean_output()),
            ],
        )
    )
    outcome = ReviewRepairLoop(adapter, lane=hunt).run()
    assert outcome.repair_rounds >= 1
    assert outcome.status == "HUNT_CLEAN"


def test_t10_git_preflight_ok_propagated_on_parsed_review(monkeypatch):
    raw = _INVEST_YALTA_TAIL.read_text(encoding="utf-8")
    loop, _adapter = _hunt_loop(raw)

    def _fake_resolve(self):
        return self.lane.review_prompt, True, None

    monkeypatch.setattr(ReviewRepairLoop, "_resolve_review_context", _fake_resolve)
    review = loop.run_single_review()
    assert review.git_preflight_ok is True


def test_t11_raw_output_sha256_stable():
    raw = _INVEST_YALTA_TAIL.read_text(encoding="utf-8")
    parsed = parse_project_bug_hunt_result(raw)
    assert raw_output_sha256(parsed.raw_output) == raw_output_sha256(raw)


def test_t12_parse_errors_empty_on_recovered_invest_yalta_tail():
    parsed = parse_project_bug_hunt_result(
        _INVEST_YALTA_TAIL.read_text(encoding="utf-8")
    )
    assert parsed.parse_errors == []


def test_t13_dual_repr_fixture_still_four_distinct_ids():
    ids = [row["finding_id"] for row in parse_project_bug_hunt_result(
        _DUAL_REPR.read_text(encoding="utf-8")
    ).findings]
    assert ids == ["PBH-001", "PBH-002", "PBH-003", "PBH-004"]


def test_t14_blocked_preflight_not_contract_invalid():
    parsed = parse_project_bug_hunt_result(
        "STATUS: BLOCKED_PREFLIGHT\n- findings_total: 0\n"
    )
    assert parsed.parse_error_code is None
    assert parsed.review_status == "BLOCKED_PREFLIGHT"


def test_t15_repair_dispatch_gate_requires_completed_review():
    """Repair gate: only valid HUNT_COMPLETE opens repair (contract invalid does not)."""
    payload = parse_project_bug_hunt_result(
        "STATUS: HUNT_COMPLETE\n- findings_total: 1\n"
    ).to_dict()
    assert payload["completed"] is False
    assert payload["parse_error_code"] == REVIEW_CONTRACT_INVALID
    good = parse_project_bug_hunt_result(
        _INVEST_YALTA_TAIL.read_text(encoding="utf-8")
    ).to_dict()
    assert good["completed"] is True
    assert json.loads(json.dumps(good))["completed"] is True
