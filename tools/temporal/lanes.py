"""Lane specs for the Temporal review→repair loop.

One worker and one task queue host every lane. Prompts, parsers, and
git-preflight policy differ; the state machine does not.

- bugbot: `/review-bugbot` + `/opsx-orch-bug-fix` (git preflight, REVIEW_SCOPE)
- security: `/review-security` skill + `/opsx-bug-team` on product-lane findings
- project-bug-hunt: `/opsx-project-bug-hunt` + `/opsx-orch-bug-fix`
  (CLI tokens base/head/change; no bugbot git preflight)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from config import (
    PROJECT_BUG_HUNT_DEFAULT_BASE,
    PROJECT_BUG_HUNT_DEFAULT_CHANGE,
    PROJECT_BUG_HUNT_DEFAULT_HEAD,
    PROJECT_BUG_HUNT_REPAIR_PROMPT,
    PROJECT_BUG_HUNT_REVIEW_PROMPT,
    REPAIR_PROMPT,
    REVIEW_BASE_REF,
    REVIEW_PROMPT,
    SECURITY_REPAIR_PROMPT,
    SECURITY_REVIEW_PATH_PREFIXES,
    SECURITY_REVIEW_PROMPT,
)
from models import RepairRunResult, ReviewRunResult
from parsers import (
    parse_bugbot_result,
    parse_opsx_bug_fix_result,
    parse_project_bug_hunt_result,
    parse_security_repair_result,
    parse_security_review_result,
)

ReviewParser = Callable[..., ReviewRunResult]
RepairParser = Callable[[str], RepairRunResult]

PROJECT_BUG_HUNT_LANE_ID = "project-bug-hunt"
SECURITY_LANE_ID = "security"


@dataclass(frozen=True)
class LaneSpec:
    """Per-command contract plugged into the shared review/repair loop."""

    id: str
    clean_status: str
    review_prompt: str
    repair_prompt: str
    git_preflight: bool
    parse_review: ReviewParser
    parse_repair: RepairParser
    workflow_type: str
    workflow_id_prefix: str
    review_activity: str
    repair_activity: str
    git_preflight_path_prefixes: tuple[str, ...] | None = None
    materialize_activity: str = "execute_commit_materialize"


BUGBOT_LANE = LaneSpec(
    id="bugbot",
    clean_status="BUGBOT_CLEAN",
    review_prompt=REVIEW_PROMPT,
    repair_prompt=REPAIR_PROMPT,
    git_preflight=True,
    parse_review=parse_bugbot_result,
    parse_repair=parse_opsx_bug_fix_result,
    workflow_type="BugbotRepairWorkflow",
    workflow_id_prefix="bugbot-repair-poc",
    review_activity="execute_bugbot_review",
    repair_activity="execute_opsx_repair",
)


def make_security_lane(*, base: str | None = None) -> LaneSpec:
    """Build security lane with concrete base branch in the review prompt."""
    resolved_base = (base or REVIEW_BASE_REF).strip()
    return LaneSpec(
        id=SECURITY_LANE_ID,
        clean_status="SECURITY_CLEAN",
        review_prompt=SECURITY_REVIEW_PROMPT.format(base=resolved_base),
        repair_prompt=SECURITY_REPAIR_PROMPT,
        git_preflight=True,
        git_preflight_path_prefixes=SECURITY_REVIEW_PATH_PREFIXES,
        parse_review=parse_security_review_result,
        parse_repair=parse_security_repair_result,
        workflow_type="SecurityReviewWorkflow",
        workflow_id_prefix="security-review-poc",
        review_activity="execute_security_review",
        repair_activity="execute_security_repair",
    )


SECURITY_LANE = make_security_lane()


def make_project_bug_hunt_lane(
    *,
    base: str | None = None,
    head: str | None = None,
    change: str | None = None,
) -> LaneSpec:
    """Build a hunt lane with concrete CLI tokens in both prompts."""
    resolved_base = (base or PROJECT_BUG_HUNT_DEFAULT_BASE).strip()
    resolved_head = (head or PROJECT_BUG_HUNT_DEFAULT_HEAD).strip()
    resolved_change = (change or PROJECT_BUG_HUNT_DEFAULT_CHANGE).strip()
    if not resolved_change:
        raise ValueError("project-bug-hunt requires a non-empty --change")
    return LaneSpec(
        id=PROJECT_BUG_HUNT_LANE_ID,
        clean_status="HUNT_CLEAN",
        review_prompt=PROJECT_BUG_HUNT_REVIEW_PROMPT.format(
            base=resolved_base,
            head=resolved_head,
            change=resolved_change,
        ),
        repair_prompt=PROJECT_BUG_HUNT_REPAIR_PROMPT.format(change=resolved_change),
        git_preflight=False,
        parse_review=parse_project_bug_hunt_result,
        parse_repair=parse_opsx_bug_fix_result,
        workflow_type="ProjectBugHuntWorkflow",
        workflow_id_prefix="project-bug-hunt-poc",
        review_activity="execute_project_bug_hunt",
        repair_activity="execute_project_bug_hunt_orch",
    )


# Default hunt lane used by mock scenarios / get_lane without overrides.
PROJECT_BUG_HUNT_LANE = make_project_bug_hunt_lane()

LANES: dict[str, LaneSpec] = {
    BUGBOT_LANE.id: BUGBOT_LANE,
    SECURITY_LANE.id: SECURITY_LANE,
    PROJECT_BUG_HUNT_LANE.id: PROJECT_BUG_HUNT_LANE,
}


def get_lane(
    lane_id: str | None,
    *,
    base: str | None = None,
    head: str | None = None,
    change: str | None = None,
) -> LaneSpec:
    name = (lane_id or BUGBOT_LANE.id).strip().lower()
    if name == PROJECT_BUG_HUNT_LANE_ID:
        return make_project_bug_hunt_lane(
            base=base or PROJECT_BUG_HUNT_DEFAULT_BASE,
            head=head or PROJECT_BUG_HUNT_DEFAULT_HEAD,
            change=change or PROJECT_BUG_HUNT_DEFAULT_CHANGE,
        )
    if name == SECURITY_LANE_ID:
        if head is not None or change is not None:
            raise ValueError(f"Lane {name!r} does not accept head= or change=")
        return make_security_lane(base=base or REVIEW_BASE_REF)
    if name not in LANES:
        known = ", ".join(sorted(LANES))
        raise ValueError(f"Unknown lane {lane_id!r}; expected one of: {known}")
    if any(v is not None for v in (base, head, change)):
        raise ValueError(f"Lane {name!r} does not accept base/head/change overrides")
    return LANES[name]
