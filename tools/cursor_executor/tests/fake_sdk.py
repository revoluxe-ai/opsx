"""In-memory fake SDK client for contract tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Sequence

from cursor_executor.schemas import ModelRoute, WritePolicy
from cursor_executor.sdk_client import SdkCreateOutcome, SdkRunOutcome, write_policy_tools


@dataclass
class FakeSdkClient:
    catalog: list[dict[str, Any]]
    create_calls: int = 0
    send_calls: int = 0
    runs: dict[str, SdkRunOutcome] = field(default_factory=dict)
    agents: dict[str, ModelRoute] = field(default_factory=dict)
    next_run: SdkRunOutcome | None = None
    fail_create: Exception | None = None
    send_delay_terminal: str | None = None
    transport_error: bool = False
    timeout_on_send: bool = False

    async def list_models(self, *, api_key: str, workspace: str) -> Sequence[Any]:
        _ = api_key, workspace
        return self.catalog

    async def create_local_agent(
        self,
        *,
        api_key: str,
        workspace: str,
        model_route: ModelRoute,
        write_policy: WritePolicy,
    ) -> SdkCreateOutcome:
        _ = api_key, workspace, write_policy
        if self.fail_create:
            raise self.fail_create
        self.create_calls += 1
        tools = write_policy_tools(write_policy)
        if write_policy == WritePolicy.READ_ONLY and tools != []:
            raise AssertionError("READ_ONLY must use tools=[]")
        agent_id = f"agent-fake-{self.create_calls}"
        self.agents[agent_id] = model_route
        return SdkCreateOutcome(agent_id=agent_id, observed_route=model_route)

    async def send_and_wait(
        self,
        *,
        api_key: str,
        agent_id: str,
        prompt: str,
        write_policy: WritePolicy,
        timeout_seconds: float | None,
    ) -> SdkRunOutcome:
        _ = api_key, prompt, write_policy, timeout_seconds
        self.send_calls += 1
        if self.transport_error:
            raise OSError("transport loss")
        if self.timeout_on_send:
            raise asyncio.TimeoutError()
        if self.next_run is None:
            raise RuntimeError("next_run not configured")
        outcome = self.next_run
        outcome.agent_id = agent_id
        self.runs[outcome.run_id] = outcome
        if self.send_delay_terminal:
            outcome.sdk_status = self.send_delay_terminal
        return outcome

    async def send_on_existing_agent(
        self,
        *,
        api_key: str,
        workspace: str,
        agent_id: str,
        prompt: str,
        write_policy: WritePolicy,
        timeout_seconds: float | None,
        model_route: ModelRoute | None = None,
    ) -> SdkRunOutcome:
        _ = model_route
        return await self.send_and_wait(
            api_key=api_key,
            agent_id=agent_id,
            prompt=prompt,
            write_policy=write_policy,
            timeout_seconds=timeout_seconds,
        )

    async def get_run_outcome(
        self,
        *,
        api_key: str,
        workspace: str,
        run_id: str,
        agent_id: str | None,
        timeout_seconds: float | None,
    ) -> SdkRunOutcome | None:
        _ = api_key, workspace, agent_id, timeout_seconds
        if self.transport_error:
            return None
        return self.runs.get(run_id)
