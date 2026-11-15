"""Visual review sub-hop и fake reviewer для тестов."""

from __future__ import annotations

from opsx.project_context import resource_path

from pathlib import Path

from agents.agent_tools.image.schemas import ImageDesignTask, VisualFinding, VisualReviewVerdict


def fake_visual_review(
    task: ImageDesignTask,
    *,
    repo_root: Path,
    force_fail: bool = False,
) -> VisualReviewVerdict:
    """Детерминированный reviewer: проверяет наличие output и rubric non-empty."""
    out = resource_path(repo_root, task.output_path)
    if force_fail:
        return VisualReviewVerdict(
            status="FAIL",
            findings=[
                VisualFinding(
                    code="VISUAL_RUBRIC",
                    message="forced fail for test",
                )
            ],
            summary="fail",
        )
    if not out.is_file():
        return VisualReviewVerdict(
            status="FAIL",
            findings=[VisualFinding(code="MISSING_OUTPUT", message="output missing")],
        )
    rubric = task.acceptance.visual_rubric.strip()
    if not rubric:
        return VisualReviewVerdict(
            status="FAIL",
            findings=[VisualFinding(code="EMPTY_RUBRIC", message="rubric empty")],
        )
    return VisualReviewVerdict(status="PASS", findings=[], summary="fake_visual_pass")


class ImageVisualReviewAgent:
    """Placeholder production hook: adapter LLM hop (см. Gate 0).

    Пока `review` делегирует в ``fake_visual_review`` + optional findings merge.
    """

    def __init__(self, *, use_llm: bool = False) -> None:
        self._use_llm = use_llm

    async def review(
        self,
        task: ImageDesignTask,
        *,
        repo_root: Path,
        prior_findings: list[VisualFinding] | None = None,
    ) -> VisualReviewVerdict:
        _ = prior_findings
        if self._use_llm:
            # LLM hop wiring — after ImageModelInputPort spike
            return fake_visual_review(task, repo_root=repo_root)
        return fake_visual_review(task, repo_root=repo_root)
