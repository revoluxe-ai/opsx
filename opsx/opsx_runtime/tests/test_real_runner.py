"""No-cost checks for the real-worktree OPSX entry point."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.adapters.abc_adapter import ABCAdapter, AgentResult
from agents.response_schema import SCHEMA_SECTION_MARKER
from agents.state import BugBotAnswer
from agents.verify.bugbot.agent import BugBotAgent
from agents.verify.bugbot.prompt.get_prompt import get_prompt
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from run import run
from run_one_node import HAIKU_LOW, create_fixture
from workflows.checkpoint_resume import invoke_checkpointed_graph
from workflows.verify.bugbot.bugbot_pipeline.bugbot_graph import build_bugbot_graph


class CaptureAdapter(ABCAdapter):
    def __init__(self):
        self.calls: list[tuple[Mapping[str, Any], str, tuple[str, ...]]] = []

    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: Any = None,
        resume_agent_id: str | None = None,
    ) -> AgentResult:
        self.calls.append((state, prompt, tuple(tools)))
        return AgentResult(
            text=json.dumps({"status": "CLEAN", "findings": [], "block_reason": None}),
            status="finished",
            agent_id="agent-test",
            run_id="run-test",
            usage={"input_tokens": 100, "output_tokens": 20, "cache_read_tokens": 0,
                   "cache_write_tokens": 0, "reasoning_tokens": None, "total_tokens": 120},
            cost={"raw_cost_cents": 0.3, "charged_cents": 0.25},
            cost_status="cursor_reported",
            usage_source="run.usage",
        )


async def _invoke_bugbot_graph(
    state: dict[str, Any],
    *,
    agent: BugBotAgent,
    checkpoint_path: Path,
    thread_id: str,
) -> dict[str, Any]:
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
        graph = build_bugbot_graph(agent, checkpointer=saver)
        return await invoke_checkpointed_graph(
            graph,
            initial_state=state,
            thread_id=thread_id,
        )


def test_bugbot_graph_preserves_exact_prompt_and_records_tokens(tmp_path: Path) -> None:
    adapter = CaptureAdapter()
    state = {"repository_path": str(tmp_path), "workspace": str(tmp_path),
             "model": HAIKU_LOW}
    checkpoint = tmp_path / "checkpoints.sqlite"
    result = asyncio.run(
        _invoke_bugbot_graph(
            state,
            agent=BugBotAgent(adapter),
            checkpoint_path=checkpoint,
            thread_id="test-one-run",
        )
    )
    assert len(adapter.calls) == 1
    _state, text, tools = adapter.calls[0]
    role_prompt = get_prompt(state, BugBotAnswer, "русский")
    assert text.startswith(role_prompt.rstrip())
    assert "BugBotAnswer" not in text
    assert text.count(SCHEMA_SECTION_MARKER) == 1
    assert tools == ("read", "grep", "glob", "ls", "shell")
    assert result["review"]["status"] == "CLEAN"
    assert result["usage"]["total_tokens"] == 120
    assert result["cost"]["charged_cents"] == 0.25
    assert checkpoint.is_file()


def test_bugbot_graph_restart_does_not_repeat_agent(tmp_path: Path) -> None:
    adapter = CaptureAdapter()
    state = {
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "model": HAIKU_LOW,
    }
    checkpoint = tmp_path / "checkpoints.sqlite"
    thread_id = "test-resume-thread"

    async def _run_twice() -> None:
        async with AsyncSqliteSaver.from_conn_string(str(checkpoint)) as saver:
            graph = build_bugbot_graph(BugBotAgent(adapter), checkpointer=saver)
            await invoke_checkpointed_graph(
                graph,
                initial_state=state,
                thread_id=thread_id,
            )
            await invoke_checkpointed_graph(
                graph,
                initial_state=state,
                thread_id=thread_id,
            )

    asyncio.run(_run_twice())
    assert len(adapter.calls) == 1


def test_entrypoint_dry_run_does_not_send_request(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    create_fixture(repo)
    meta = asyncio.run(run(workspace=repo, timeout=20, dry_run=True))
    assert meta["execution_status"] == "DRY_RUN"
    run_dir = repo / "opsx" / "runs" / "bugbot" / meta["execution_id"]
    assert (run_dir / "execution.json").exists()
    assert not (run_dir / "review.json").exists()
    assert not (run_dir / "checkpoints.sqlite").exists()
