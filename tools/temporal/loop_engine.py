"""Shared review/repair loop engine (usable with or without Temporal).

Lane specs supply prompts and parsers. BugbotRepairLoop keeps the original
constructor and clean status so existing tests and activities stay stable.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from config import (
    GIT_PREFLIGHT_ARTIFACT_REL,
    MAX_REPAIR_ROUNDS,
    MAX_REVIEW_RUNS,
    REVIEW_BASE_REF,
    REVIEW_TECHNICAL_RETRY_MAX,
    SECURITY_REVIEW_ARTIFACT_FALLBACK_PROMPT,
)
from commit_materialization import (
    COMMIT_MATERIALIZE_BLOCKED,
    MaterializeResult,
    current_head,
    materialize_if_needed,
)
from cursor_adapter import CursorAdapter, CursorAdapterError, CursorTransientError
from git_preflight import build_review_prompt, collect_git_preflight
from lanes import BUGBOT_LANE, PROJECT_BUG_HUNT_LANE_ID, SECURITY_LANE_ID, LaneSpec, make_project_bug_hunt_lane
from models import LoopOutcome, RepairRunResult, ReviewRunResult
from parsers import is_security_preflight_blocker_only, is_transient_error
from repair_progress_evidence import (
    apply_durable_repair_progress,
    product_worktree_fingerprint,
)


@dataclass
class LoopCounters:
    repair_rounds: int = 0
    review_runs: int = 0


class ReviewRepairLoop:
    """State machine that mirrors the Owner's manual Cursor prompt sequence."""

    def __init__(
        self,
        adapter: CursorAdapter,
        *,
        lane: LaneSpec | None = None,
        repo_path: str | None = None,
        base_ref: str = REVIEW_BASE_REF,
        head: str | None = None,
        change: str | None = None,
        mock_mode: bool = False,
    ) -> None:
        self.adapter = adapter
        self.lane = lane or BUGBOT_LANE
        adapter_repo = getattr(adapter, "repo_path", None)
        self.repo_path = repo_path or (str(adapter_repo) if adapter_repo else None)
        self.base_ref = base_ref
        self._lane_head = head
        self._lane_change = change
        self.mock_mode = mock_mode
        self.candidate_head = None
        self.last_materialize: MaterializeResult | None = None
        self.counters = LoopCounters()
        self.review_history: list[ReviewRunResult] = []
        self.repair_history: list[RepairRunResult] = []
        self._consecutive_same_fp_reviews = 0
        self._last_fp: str | None = None

    def run(self) -> LoopOutcome:
        while True:
            review = self._run_review_with_technical_retry()
            self.review_history.append(review)
            self.counters.review_runs += 1

            if not review.completed:
                return self._outcome(
                    "REVIEW_FAILED",
                    f"Review did not complete: {review.raw_output[:300]}",
                    review=review,
                )

            if review.clean:
                if review.excluded_findings and not review.repairable_findings:
                    message = (
                        "Product lane clean after POC filter; "
                        f"{len(review.excluded_findings)} process finding(s) excluded from automation."
                    )
                else:
                    message = "Review reported no actionable findings."
                return self._outcome(self.lane.clean_status, message, review=review)

            if self.counters.repair_rounds >= MAX_REPAIR_ROUNDS:
                return self._outcome(
                    "MAX_ROUNDS_REACHED",
                    (
                        f"Final review after repair #{self.counters.repair_rounds} "
                        "still has actionable findings."
                    ),
                    review=review,
                )

            if self._is_stalled(review):
                return self._outcome(
                    "STALLED",
                    "Actionable findings fingerprint unchanged across repair rounds.",
                    review=review,
                )

            if self.counters.review_runs >= MAX_REVIEW_RUNS:
                return self._outcome(
                    "MAX_ROUNDS_REACHED",
                    "Maximum review run budget reached.",
                    review=review,
                )

            repair = self._run_repair_once()
            self.repair_history.append(repair)
            if repair.uncertain:
                return self._outcome(
                    "REPAIR_COMPLETION_UNCERTAIN",
                    "Repair invocation ended ambiguously after possible side effects.",
                    review=review,
                    repair=repair,
                )
            if not repair.completed:
                return self._outcome(
                    "REPAIR_FAILED",
                    f"Repair did not reach terminal status: {repair.raw_output[:300]}",
                    review=review,
                    repair=repair,
                )

            self.counters.repair_rounds += 1

            # Owner contract: nothing repaired this batch → stop (even if
            # BUG_BATCH_CLEAR after defer-only / filter-only orch).
            if repair.repaired_this_batch is False:
                return self._outcome(
                    "NO_REPAIR_PROGRESS",
                    "Orch completed with nothing repaired this batch; stopping loop.",
                    review=review,
                    repair=repair,
                )

            mat = self._materialize_after_repair(repair)
            self.last_materialize = mat
            if not mat.ok:
                return self._outcome(
                    COMMIT_MATERIALIZE_BLOCKED,
                    (
                        "Post-repair /opsx-commit-all GO did not produce a clean "
                        "committed candidate; next review is forbidden. "
                        f"reason={mat.reason}"
                    ),
                    review=review,
                    repair=repair,
                )
            self._refresh_candidate(mat)
            if mat.candidate_transitioned:
                self._consecutive_same_fp_reviews = 0
                self._last_fp = None

    def run_single_review(self) -> ReviewRunResult:
        return self._run_review_with_technical_retry()

    def run_single_repair(self) -> RepairRunResult:
        return self._run_repair_once()

    @staticmethod
    def update_stagnation_state(
        fingerprint: str | None,
        *,
        last_fp: str | None,
        consecutive_same_fp_reviews: int,
    ) -> tuple[str | None, int]:
        if not fingerprint:
            return None, 0
        if fingerprint == last_fp:
            return fingerprint, consecutive_same_fp_reviews + 1
        return fingerprint, 1

    @staticmethod
    def is_stalled(repair_rounds: int, consecutive_same_fp_reviews: int) -> bool:
        return repair_rounds >= 2 and consecutive_same_fp_reviews >= 3

    def _reset_review_session(self, *, reason: str) -> None:
        self.adapter.reset_session(reason=reason)

    @staticmethod
    def _should_retry_review(parsed: ReviewRunResult, attempt: int) -> bool:
        if attempt >= REVIEW_TECHNICAL_RETRY_MAX:
            return False
        return parsed.transient_error or not parsed.completed

    def _empty_review(self, raw_output: str, *, transient: bool = False) -> ReviewRunResult:
        return ReviewRunResult(
            completed=False,
            clean=None,
            findings=[],
            actionable_findings=[],
            repairable_findings=[],
            excluded_findings=[],
            fingerprint=None,
            raw_output=raw_output,
            transient_error=transient,
        )

    def _build_security_artifact_fallback_prompt(self) -> str:
        return SECURITY_REVIEW_ARTIFACT_FALLBACK_PROMPT.format(
            artifact_path=GIT_PREFLIGHT_ARTIFACT_REL,
            base=self.base_ref,
        )

    def _run_security_artifact_fallback(self, *, git_preflight_ok: bool) -> ReviewRunResult:
        self._reset_review_session(reason="security-artifact-fallback")
        fallback_prompt = self._build_security_artifact_fallback_prompt()
        try:
            invocation = self.adapter.execute_prompt(
                fallback_prompt,
                step="review-fallback",
            )
        except CursorTransientError as exc:
            return self._empty_review(str(exc), transient=True)
        except CursorAdapterError as exc:
            return self._empty_review(str(exc))
        return self.lane.parse_review(
            invocation.raw_output,
            git_preflight_ok=git_preflight_ok,
        )

    def _resolve_review_context(self) -> tuple[str, bool, str | None]:
        prompt = self.lane.review_prompt
        if not self.lane.git_preflight or not self.repo_path:
            if self.repo_path and not self.candidate_head:
                self.candidate_head = current_head(self.repo_path)
            return prompt, False, None
        preflight = collect_git_preflight(
            self.repo_path,
            base_ref=self.base_ref,
            path_prefixes=self.lane.git_preflight_path_prefixes,
        )
        if not preflight.ok:
            return prompt, False, preflight.error or "git preflight failed"
        self.candidate_head = preflight.head_ref
        return (
            build_review_prompt(
                preflight,
                repo_path=self.repo_path,
                review_prompt=prompt,
            ),
            True,
            None,
        )

    def _run_review_with_technical_retry(self) -> ReviewRunResult:
        if self.counters.repair_rounds > 0:
            self._reset_review_session(reason="post-repair-round")

        prompt, git_preflight_ok, git_error = self._resolve_review_context()
        if git_error:
            return self._empty_review(f"Git preflight failed: {git_error}")

        last_result: ReviewRunResult | None = None
        for attempt in range(REVIEW_TECHNICAL_RETRY_MAX + 1):
            if attempt > 0:
                self._reset_review_session(reason=f"review-retry-{attempt}")

            try:
                invocation = self.adapter.execute_prompt(prompt, step="review")
            except CursorTransientError as exc:
                last_result = self._empty_review(str(exc), transient=True)
                if self._should_retry_review(last_result, attempt):
                    continue
                return last_result
            except CursorAdapterError as exc:
                msg = str(exc)
                transient = is_transient_error(msg)
                last_result = self._empty_review(msg, transient=transient)
                if self._should_retry_review(last_result, attempt):
                    continue
                return last_result

            parsed = self.lane.parse_review(
                invocation.raw_output,
                git_preflight_ok=git_preflight_ok,
            )
            parsed.candidate_sha = self.candidate_head
            if (
                git_preflight_ok
                and self.lane.id == SECURITY_LANE_ID
                and is_security_preflight_blocker_only(parsed.raw_output)
            ):
                return self._run_security_artifact_fallback(git_preflight_ok=git_preflight_ok)
            if self._should_retry_review(parsed, attempt):
                last_result = parsed
                continue
            return parsed

        return last_result or self._empty_review(
            "review technical retry exhausted",
            transient=True,
        )

    def _run_repair_once(self) -> RepairRunResult:
        before_fp = ""
        repair_started_at = time.monotonic()
        if self.repo_path:
            before_fp = product_worktree_fingerprint(self.repo_path)
        try:
            invocation = self.adapter.execute_prompt(self.lane.repair_prompt, step="repair")
        except CursorTransientError as exc:
            return RepairRunResult(
                completed=False,
                bug_batch_clear=None,
                unresolved=[],
                running_worker=None,
                raw_output=str(exc),
                uncertain=True,
            )
        except CursorAdapterError as exc:
            return RepairRunResult(
                completed=False,
                bug_batch_clear=None,
                unresolved=[],
                running_worker=None,
                raw_output=str(exc),
                uncertain=True,
            )

        parsed = self.lane.parse_repair(invocation.raw_output)
        if invocation.exit_code != 0 and not parsed.completed:
            parsed.uncertain = True
        parsed = apply_durable_repair_progress(
            parsed,
            self.repo_path,
            before_fingerprint=before_fp,
            since_monotonic=repair_started_at,
        )
        return parsed

    def _materialize_after_repair(self, repair: RepairRunResult) -> MaterializeResult:
        return materialize_if_needed(
            repo_path=self.repo_path,
            adapter=self.adapter,
            repair=repair,
            mock_mode=self.mock_mode,
        )

    def _refresh_candidate(self, mat: MaterializeResult) -> None:
        if mat.head_after:
            self.candidate_head = mat.head_after
            self._lane_head = mat.head_after
        elif self.repo_path:
            live = current_head(self.repo_path)
            if live:
                self.candidate_head = live
        if self.lane.id == PROJECT_BUG_HUNT_LANE_ID and self._lane_head:
            self.lane = make_project_bug_hunt_lane(
                base=self.base_ref,
                head=self._lane_head,
                change=self._lane_change,
            )

    def _is_stalled(self, review: ReviewRunResult) -> bool:
        fp = review.fingerprint
        if not fp:
            self._consecutive_same_fp_reviews = 0
            self._last_fp = None
            return False

        if fp == self._last_fp:
            self._consecutive_same_fp_reviews += 1
        else:
            self._consecutive_same_fp_reviews = 1
            self._last_fp = fp

        return self.counters.repair_rounds >= 2 and self._consecutive_same_fp_reviews >= 3

    def _outcome(
        self,
        status: str,
        message: str,
        *,
        review: ReviewRunResult | None = None,
        repair: RepairRunResult | None = None,
    ) -> LoopOutcome:
        if repair is None and self.repair_history:
            repair = self.repair_history[-1]
        return LoopOutcome(
            status=status,
            repair_rounds=self.counters.repair_rounds,
            review_runs=self.counters.review_runs,
            message=message,
            last_review=review,
            last_repair=repair,
            session_id=self.adapter.session_id(),
            review_history=list(self.review_history),
            repair_history=list(self.repair_history),
            last_materialize=(
                self.last_materialize.to_dict() if self.last_materialize else None
            ),
            candidate_sha=self.candidate_head,
        )


class BugbotRepairLoop(ReviewRepairLoop):
    """Backward-compatible bugbot lane (default Temporal POC)."""

    def __init__(
        self,
        adapter: CursorAdapter,
        *,
        repo_path: str | None = None,
        base_ref: str = REVIEW_BASE_REF,
        head: str | None = None,
        change: str | None = None,
        mock_mode: bool = False,
    ) -> None:
        super().__init__(
            adapter,
            lane=BUGBOT_LANE,
            repo_path=repo_path,
            base_ref=base_ref,
            head=head,
            change=change,
            mock_mode=mock_mode,
        )
