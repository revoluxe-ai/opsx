"""Legacy hermetic stub — disabled for production orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import product_writer_global

WRITER = "/opsx-bug-team"


class BugTeamEpisodeError(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def acquire_product_writer(repo_root: Path, *, mission_id: str, execution_id: str) -> None:
    try:
        product_writer_global.acquire(
            repo_root,
            mission_id=mission_id,
            execution_id=execution_id,
            finding_id="legacy-lock",
        )
    except product_writer_global.ProductWriterError as error:
        raise BugTeamEpisodeError(error.reason) from error


def release_product_writer(repo_root: Path, *, mission_id: str, execution_id: str) -> None:
    product_writer_global.release_on_terminal(
        repo_root,
        mission_id=mission_id,
        execution_id=execution_id,
        execution_terminal="CONFIRMED",
    )


def complete_episode_hermetic(
    repo_root: Path,
    *,
    mission_id: str,
    execution_id: str,
    head: str,
    case_id: str,
    finding_id: str,
    plan_id: str,
    work_item_id: str,
) -> dict[str, Any]:
    raise BugTeamEpisodeError(
        "HERMETIC_STUB_ISOLATED_USE_HERMETIC_TEST_EXECUTOR_WITH_AUTHORIZED_REQUEST"
    )
