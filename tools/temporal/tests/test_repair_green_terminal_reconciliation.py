"""Regression: STATUS GREEN terminal + in-scope local-write reconciliation."""

from __future__ import annotations

import subprocess
from pathlib import Path

from loop_engine import ReviewRepairLoop
from models import repair_authorizes_materialize
from parsers import parse_opsx_bug_fix_result
from recovery_contract import (
    PAUSE_UNRECONCILED,
    UNCERTAIN,
    contract_for_uncertain,
    refuse_unreconciled_retry,
)
from write_ownership import capture_worktree, worktree_drift_is_authorized_only


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True, capture_output=True)
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/app.py"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)


def test_status_green_is_terminal_not_uncertain() -> None:
    raw = """STATUS: GREEN
REPAIRED_THIS_BATCH: YES
BUG_BATCH_CLEAR: NO
"""
    parsed = parse_opsx_bug_fix_result(raw)
    assert parsed.completed is True
    assert parsed.uncertain is False
    assert parsed.terminal_status == "GREEN"
    assert parsed.bug_batch_clear is False
    assert repair_authorizes_materialize(
        terminal_status=parsed.terminal_status,
        repaired_this_batch=parsed.repaired_this_batch,
        bug_batch_clear=parsed.bug_batch_clear,
    ) is False


def test_authorized_local_drift_reconciles_uncertain_contract(tmp_path: Path) -> None:
    _git_init(tmp_path)
    baseline = capture_worktree(tmp_path)
    (tmp_path / "src" / "app.py").write_text("v2\n", encoding="utf-8")
    assert worktree_drift_is_authorized_only(tmp_path, baseline, ("src",)) is True
    contract = contract_for_uncertain(
        repo_available=True,
        inspected=True,
        side_effects_match_baseline=False,
        authorized_local_writes_only=True,
    )
    assert contract.status == UNCERTAIN
    assert contract.reconciled is True
    assert refuse_unreconciled_retry(contract) is None


def test_foreign_path_drift_not_reconciled(tmp_path: Path) -> None:
    _git_init(tmp_path)
    baseline = capture_worktree(tmp_path)
    (tmp_path / "notes.txt").write_text("x\n", encoding="utf-8")
    assert worktree_drift_is_authorized_only(tmp_path, baseline, ("src",)) is False
    contract = contract_for_uncertain(
        repo_available=True,
        inspected=True,
        side_effects_match_baseline=False,
        authorized_local_writes_only=False,
    )
    assert contract.status == PAUSE_UNRECONCILED
    assert contract.reconciled is False
    assert refuse_unreconciled_retry(contract) is not None


class _RepairAdapter:
    def __init__(self, raw: str) -> None:
        self._raw = raw

    def session_id(self) -> str:
        return "t"

    def reset_session(self, *, reason: str) -> None:
        del reason

    def execute_prompt(self, prompt: str, *, step: str | None = None):
        from cursor_adapter import PromptInvocation

        del prompt, step
        return PromptInvocation(
            prompt="",
            raw_output=self._raw,
            exit_code=0,
            session_id="t",
        )


def test_single_repair_green_with_in_scope_write_is_not_uncertain(tmp_path: Path) -> None:
    """Orch STATUS GREEN + in-scope product patch must not mark the hop uncertain."""
    from cursor_adapter import PromptInvocation
    from lanes import BUGBOT_LANE

    _git_init(tmp_path)
    repair_raw = "STATUS: GREEN\nREPAIRED_THIS_BATCH: YES\nBUG_BATCH_CLEAR: NO\n"

    class _PatchingAdapter:
        def session_id(self) -> str:
            return "t"

        def reset_session(self, *, reason: str) -> None:
            del reason

        def execute_prompt(self, prompt: str, *, step: str | None = None) -> PromptInvocation:
            del prompt
            (tmp_path / "src" / "app.py").write_text("patched\n", encoding="utf-8")
            return PromptInvocation(
                prompt="",
                raw_output=repair_raw,
                exit_code=0,
                session_id="t",
            )

    repair = ReviewRepairLoop(
        _PatchingAdapter(),
        lane=BUGBOT_LANE,
        repo_path=str(tmp_path),
        mock_mode=False,
        authorized_prefixes=("src",),
    ).run_single_repair()
    assert repair.uncertain is False
    assert repair.terminal_status == "GREEN"
    assert repair.recovery_status is None


def test_single_repair_prose_with_in_scope_write_reconciles_not_pause(tmp_path: Path) -> None:
    """Legacy orch prose without STATUS reconciles in-scope writes without PAUSE."""
    from cursor_adapter import PromptInvocation
    from lanes import BUGBOT_LANE

    _git_init(tmp_path)

    class _PatchingAdapter:
        def session_id(self) -> str:
            return "t"

        def reset_session(self, *, reason: str) -> None:
            del reason

        def execute_prompt(self, prompt: str, *, step: str | None = None) -> PromptInvocation:
            del prompt
            (tmp_path / "src" / "app.py").write_text("patched\n", encoding="utf-8")
            return PromptInvocation(
                prompt="",
                raw_output="Applied fix; tests GREEN in prose but no STATUS line.\n",
                exit_code=0,
                session_id="t",
            )

    repair = ReviewRepairLoop(
        _PatchingAdapter(),
        lane=BUGBOT_LANE,
        repo_path=str(tmp_path),
        mock_mode=False,
        authorized_prefixes=("src",),
    ).run_single_repair()
    assert repair.uncertain is True
    assert repair.recovery_status == UNCERTAIN
    assert repair.recovery_status != PAUSE_UNRECONCILED
    assert "authorized in-scope" in (repair.recovery_message or "").lower()
