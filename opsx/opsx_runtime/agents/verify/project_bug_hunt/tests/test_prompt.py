"""Needle-тесты промпта ProjectBugHuntAgent."""

from __future__ import annotations

from agents.state import GraphState
from agents.verify.project_bug_hunt.prompt.get_prompt import get_prompt
from agents.verify.project_bug_hunt.schemas import ProjectBugHuntAnswer


def test_prompt_contains_stroy_override_and_classification_vocabulary():
    prompt = get_prompt(
        GraphState(repository_path="/repo", change_id="my-change"),
        ProjectBugHuntAnswer,
        "русский",
    )
    assert "STROY-SYSTEM TARGET OVERRIDE" in prompt
    assert "confirmed production bug" in prompt
    assert "reachable but low-risk" in prompt
    assert "loc_link" in prompt
    assert "my-change" in prompt
    assert "READ_ONLY" in prompt
