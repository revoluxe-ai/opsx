"""Unit-тесты ``CheckWorkflowReceipt``: check / establish_patch."""

from __future__ import annotations

from pathlib import Path

import pytest

from check_workflow_receipt import (
    CheckWorkflowReceipt,
    CheckWorkflowReceiptStatus,
    bugbot_workflow_receipt,
    project_bug_workflow_receipt,
    security_workflow_receipt,
)
from workflows.verify.cycle_checks import (
    CYCLE_CHECKS_STATE_KEY,
    CheckName,
    cycle_checks,
    register_check_workflow_receipt,
)


def _state(repo: Path, *, change_id: str = "my-change") -> dict:
    return {
        "repository_path": str(repo),
        "change_id": change_id,
    }


@pytest.fixture
def mini_repo(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    change = tmp_path / "openspec" / "changes" / "my-change"
    change.mkdir(parents=True)
    (change / "proposal.md").write_text("# p\n", encoding="utf-8")
    return tmp_path


def test_check_not_established_then_match_after_patch(mini_repo: Path) -> None:
    receipt = CheckWorkflowReceipt(CheckName.PROJECT_BUG, ("src",))
    state = _state(mini_repo)
    assert receipt.check(state) is CheckWorkflowReceiptStatus.NOT_ESTABLISHED
    patch = receipt.establish_patch(state)
    merged = {**state, **patch}
    assert receipt.check(merged) is CheckWorkflowReceiptStatus.MATCH


def test_check_stale_after_file_change(mini_repo: Path) -> None:
    receipt = CheckWorkflowReceipt(CheckName.PROJECT_BUG, ("src",))
    state = _state(mini_repo)
    merged = {**state, **receipt.establish_patch(state)}
    assert receipt.check(merged) is CheckWorkflowReceiptStatus.MATCH
    (mini_repo / "src" / "app.py").write_text("x = 2\n", encoding="utf-8")
    assert receipt.check(merged) is CheckWorkflowReceiptStatus.STALE


@pytest.mark.parametrize(
    ("factory", "check_name"),
    [
        (project_bug_workflow_receipt, CheckName.PROJECT_BUG),
        (bugbot_workflow_receipt, CheckName.BUGBOT),
        (security_workflow_receipt, CheckName.SECURITY),
    ],
)
def test_cycle_checks_is_established_delegates_to_receipt(
    mini_repo: Path,
    factory,
    check_name: CheckName,
) -> None:
    receipt = factory()
    register_check_workflow_receipt(receipt)
    state = _state(mini_repo)
    assert not cycle_checks(state).is_established(check_name)
    merged = {**state, **receipt.establish_patch(state)}
    assert cycle_checks(merged).is_established(check_name)


def test_bugbot_and_security_established_keys_do_not_overwrite(mini_repo: Path) -> None:
    """Разные ``CheckName`` — независимые ключи в ``cycle_checks.established``."""
    bugbot = bugbot_workflow_receipt()
    security = security_workflow_receipt()
    register_check_workflow_receipt(bugbot)
    register_check_workflow_receipt(security)
    state = _state(mini_repo)
    merged = {**state, **bugbot.establish_patch(state)}
    merged = {**merged, **security.establish_patch(merged)}
    established = (merged.get(CYCLE_CHECKS_STATE_KEY) or {}).get("established") or {}
    assert CheckName.BUGBOT.value in established
    assert CheckName.SECURITY.value in established
    assert cycle_checks(merged).is_established(CheckName.BUGBOT)
    assert cycle_checks(merged).is_established(CheckName.SECURITY)


def test_digest_unavailable_without_repo() -> None:
    receipt = CheckWorkflowReceipt(CheckName.PROJECT_BUG, ("src",))
    assert receipt.check({}) is CheckWorkflowReceiptStatus.DIGEST_UNAVAILABLE
