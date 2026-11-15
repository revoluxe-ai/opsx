"""Needle-тесты промпта SecurityReviewAgent."""

from __future__ import annotations

from agents.state import GraphState
from agents.verify.security_review.prompt.get_prompt import get_prompt
from agents.verify.security_review.schemas import SecurityReviewAnswer


def test_prompt_contains_review_security_and_stroy_scope():
    prompt = get_prompt(
        GraphState(repository_path="/repo", change_id="sec-change"),
        SecurityReviewAnswer,
        "русский",
    )
    assert "/review-security" in prompt
    assert "STROY-SYSTEM TARGET OVERRIDE" in prompt
    assert "`src/**` and `tests/**`" in prompt
    assert "process_lane" in prompt
    assert ".cursor/**" in prompt
    assert "confirmed security issue" in prompt
    assert "sec-change" in prompt
    assert "READ_ONLY" in prompt
    assert "opsx-security-review" in prompt
