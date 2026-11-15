"""P0: ensure_verification_context accepts TARGET bounded-verify/plan.json only."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools" / "temporal"))

from activities import ensure_verification_context  # noqa: E402
from activities import configure_activities, ActivityConfig  # noqa: E402


EXTERNAL_CLIENT_PLAN_ID = "456dfd9f-9fac-4060-8e4d-43f7f9e153b9"
EXTERNAL_CLIENT_FINGERPRINT = (
    "233b11e8f8dd2d1925600a3a999ab4a2bbb7a5c1cfa8a24179f945e1f27ddc22"
)


def _write_external_client_fixture(root: Path) -> None:
    change = "external-client-notifications-pilot"
    cdir = root / "openspec" / "changes" / change / "bounded-verify"
    cdir.mkdir(parents=True, exist_ok=True)
    plan = {
        "schema_version": "bounded-verify-plan-v1",
        "change_id": change,
        "plan_id": EXTERNAL_CLIENT_PLAN_ID,
        "plan_status": "FROZEN",
        "plan_fingerprint": EXTERNAL_CLIENT_FINGERPRINT,
        "required_checks": [
            {"check_id": "pyright-strict", "order": 1},
            {"check_id": "mypy", "order": 2},
            {"check_id": "pytest-targeted", "order": 3},
            {"check_id": "pytest-unit", "order": 4},
            {"check_id": "pytest-integration", "order": 5},
        ],
        "implementation_snapshot": {"content_digest": "fixture-digest"},
    }
    (cdir / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    # Deliberately no verify/VERIFICATION-PLAN.json — SOURCE carrier must not be required.


def test_ensure_verification_context_reads_bounded_plan_only(tmp_path: Path) -> None:
    _write_external_client_fixture(tmp_path)
    configure_activities(
        ActivityConfig(repo_path=tmp_path, mode="real", mock_scenario=None)
    )
    result = asyncio.run(
        ensure_verification_context({"change": "external-client-notifications-pilot"})
    )
    assert result["status"] == "OK"
    assert result["plan_id"] == EXTERNAL_CLIENT_PLAN_ID
    assert "VERIFICATION-PLAN" not in (result.get("message") or "")


def test_missing_bounded_plan_still_blocked_not_source_path(tmp_path: Path) -> None:
    configure_activities(
        ActivityConfig(repo_path=tmp_path, mode="real", mock_scenario=None)
    )
    result = asyncio.run(
        ensure_verification_context({"change": "external-client-notifications-pilot"})
    )
    assert result["status"] == "BLOCKED"
    assert result["block_reason"] == "VERIFICATION_PLAN_MISSING"
    assert "bounded-verify/plan.json" in result["message"]
