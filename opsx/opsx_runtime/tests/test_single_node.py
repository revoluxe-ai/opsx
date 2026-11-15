"""Unit checks for one-node Bugbot without paid Cursor API calls."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.adapters.abc_adapter import ABCAdapter, AgentResult
from agents.adapters.provider_bootstrap import ensure_adapter_factories_registered
from agents.adapters.provider_registry import (
    build_review_adapter,
    call_bootstrap_hook,
)
from agents.state import BugBotAnswer
from agents.verify.bugbot.agent import BugBotAgent
from workflows.verify.bugbot.bugbot_pipeline.bugbot_graph import build_bugbot_graph
from run_one_node import HAIKU_LOW, REPO_ROOT, create_fixture
from run import git, resolve_review_scope


def _review_adapter(**kwargs: object) -> ABCAdapter:
    ensure_adapter_factories_registered()
    return build_review_adapter(**kwargs)


class StubAdapter(ABCAdapter):
    def __init__(self, text: str):
        self.text = text
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: Any = None,
        resume_agent_id: str | None = None,
    ) -> AgentResult:
        self.calls.append((prompt, tuple(tools)))
        return AgentResult(
            text=self.text,
            status="finished",
            agent_id="agent-test",
            run_id="run-test",
            usage={
                "input_tokens": 31,
                "output_tokens": 13,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "reasoning_tokens": None,
                "total_tokens": 44,
            },
            cost={"raw_cost_cents": 0.05, "charged_cents": 0.03},
            usage_source="stub",
            cost_status="cursor_reported",
        )


def test_single_node_graph_collects_review_usage_and_cost(tmp_path: Path) -> None:
    sample = json.dumps(
        {
            "status": "FINDINGS",
            "findings": [
                {
                    "severity": "high",
                    "location": "admission.py:2",
                    "finding": "age=20 -> reversed predicate -> eligible=False",
                }
            ],
            "block_reason": None,
        }
    )
    adapter = StubAdapter(sample)
    graph = build_bugbot_graph(BugBotAgent(adapter))
    state = {
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "model": HAIKU_LOW,
        "isolated_workspace": False,
    }
    result = asyncio.run(graph.ainvoke(state))
    assert result["review"]["status"] == "FINDINGS"
    assert result["review"]["findings"][0]["location"] == "admission.py:2"
    assert result["usage"]["total_tokens"] == 44
    assert result["cost"]["charged_cents"] == 0.03
    assert result["agent_id"] == "agent-test"
    assert len(adapter.calls) == 1
    assert adapter.calls[0][1] == ("read", "grep", "glob", "ls", "shell")
    assert "VERIFIED GIT DIFF" not in adapter.calls[0][0]
    assert "Read the complete branch diff against" in adapter.calls[0][0]


def test_invalid_json_not_silently_marked_clean(tmp_path: Path) -> None:
    graph = build_bugbot_graph(BugBotAgent(StubAdapter("not json")))
    with pytest.raises(ValidationError):
        asyncio.run(
            graph.ainvoke(
                {
                    "repository_path": str(tmp_path),
                    "workspace": str(tmp_path),
                    "model": HAIKU_LOW,
                    "isolated_workspace": False,
                }
            )
        )


def test_invalid_tools_rejected_before_sdk(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="read/grep/glob/ls/shell"):
        asyncio.run(
            _review_adapter().run(
                {"workspace": str(tmp_path), "model": HAIKU_LOW},
                prompt="test",
                tools=["write"],
            )
        )


def test_schema_rejects_contradictory_clean_state() -> None:
    with pytest.raises(ValidationError):
        BugBotAnswer.model_validate(
            {
                "status": "CLEAN",
                "findings": [
                    {
                        "severity": "low",
                        "location": "foo.py:1",
                        "finding": "defect",
                    }
                ],
            }
        )


def test_sdk_repo_path() -> None:
    assert (REPO_ROOT / "tools" / "cursor_executor").is_dir()


def test_usage_missing_not_replaced_with_zero() -> None:
    ensure_adapter_factories_registered()
    token_counts = call_bootstrap_hook("token_counts_from_usage")
    assert token_counts(None) is None


def test_git_fixture_has_a_real_stage_diff(tmp_path: Path) -> None:
    diff = create_fixture(tmp_path / "fixture")
    assert "-    return age >= 18" in diff
    assert "+    return age < 18" in diff

def test_review_scope_has_stage_head_and_diff(tmp_path: Path) -> None:
    source = tmp_path / "fixture"
    create_fixture(source)
    identity = resolve_review_scope(source)
    assert identity["branch"] == "feature/bugbot-smoke"
    assert git(source, "rev-parse", "HEAD") == identity["head"]
    assert git(source, "rev-parse", "stage") == identity["stage"]
    assert git(source, "diff", "--name-only", "stage...HEAD") == "admission.py"


def test_shell_requires_allow_git_shell(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="shell opt-in"):
        asyncio.run(
            _review_adapter(allow_git_shell=False).run(
                {"workspace": str(tmp_path), "model": HAIKU_LOW},
                prompt="test",
                tools=["shell"],
            )
        )

def test_model_fenced_json_is_validated_without_changing_prompt() -> None:
    from agents.verify.bugbot.agent import parse_review
    content = (
        "```json\n"
        '{"status":"CLEAN","findings":[],"block_reason":null}'
        "\n```"
    )
    assert parse_review(content).status == "CLEAN"


def test_model_fenced_json_with_surrounding_prose_is_rejected() -> None:
    from agents.verify.bugbot.agent import parse_review
    with pytest.raises(ValidationError):
        parse_review(
            "Please see below:\n```json\n"
            '{"status":"CLEAN","findings":[],"block_reason":null}\n```'
        )
