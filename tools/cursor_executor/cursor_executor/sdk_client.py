"""Thin async SDK boundary with injectable fake for tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from .bridge_local import agent_local_options, bridge_launch_kwargs
from .schemas import ModelRoute, RuntimeKind, WritePolicy, model_route_from_sdk_model


@dataclass
class SdkRunOutcome:
    run_id: str
    agent_id: str
    sdk_status: str
    result_text: str
    observed_route: ModelRoute | None
    duration_ms: int | None
    sdk_status_message: str | None = None


@dataclass
class SdkCreateOutcome:
    agent_id: str
    observed_route: ModelRoute | None


class SdkClient(Protocol):
    async def list_models(self, *, api_key: str, workspace: str) -> Sequence[Any]:
        ...

    async def create_local_agent(
        self,
        *,
        api_key: str,
        workspace: str,
        model_route: ModelRoute,
        write_policy: WritePolicy,
    ) -> SdkCreateOutcome:
        ...

    async def send_and_wait(
        self,
        *,
        api_key: str,
        agent_id: str,
        prompt: str,
        write_policy: WritePolicy,
        timeout_seconds: float | None,
    ) -> SdkRunOutcome:
        ...

    async def get_run_outcome(
        self,
        *,
        api_key: str,
        workspace: str,
        run_id: str,
        agent_id: str | None,
        timeout_seconds: float | None,
    ) -> SdkRunOutcome | None:
        ...

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
        ...


def write_policy_tools(write_policy: WritePolicy) -> list[str] | None:
    if write_policy == WritePolicy.READ_ONLY:
        return []
    return None


def write_policy_enforcement_label(write_policy: WritePolicy) -> str:
    if write_policy == WritePolicy.READ_ONLY:
        return "NO_BUILTIN_TOOLS"
    return "SDK_DEFAULT_TOOLSET"


def model_route_to_sdk_selection(route: ModelRoute) -> dict[str, Any]:
    return {
        "id": route.model_id,
        "params": [{"id": k, "value": v} for k, v in route.normalized_params().items()],
    }


class AsyncCursorSdkClient:
    """Real cursor-sdk async bridge client (Windows-safe async path)."""

    async def list_models(self, *, api_key: str, workspace: str) -> Sequence[Any]:
        from cursor_sdk.asyncio import AsyncClient, AsyncCursor

        client = await AsyncClient.launch_bridge(
            **bridge_launch_kwargs(agent_cwd=workspace),
        )
        try:
            return await AsyncCursor.models.list(client=client, api_key=api_key)
        finally:
            await client.aclose()

    async def _with_workspace_client(self, workspace: str):
        from cursor_sdk.asyncio import AsyncClient

        return await AsyncClient.launch_bridge(**bridge_launch_kwargs(agent_cwd=workspace))

    async def create_local_agent(
        self,
        *,
        api_key: str,
        workspace: str,
        model_route: ModelRoute,
        write_policy: WritePolicy,
    ) -> SdkCreateOutcome:
        from cursor_sdk.asyncio import AsyncAgent

        tools = write_policy_tools(write_policy)
        options: dict[str, Any] = {
            "model": model_route_to_sdk_selection(model_route),
            "apiKey": api_key,
            "local": agent_local_options(agent_cwd=workspace),
        }
        if tools is not None:
            options["tools"] = tools

        client = await self._with_workspace_client(workspace)
        self._active_client = client
        try:
            agent = await AsyncAgent.create(options, client=client)
            observed = model_route_from_sdk_model(
                agent.model.to_json() if hasattr(agent.model, "to_json") and agent.model else None
            )
            self._active_agent_id = agent.agent_id
            return SdkCreateOutcome(agent_id=agent.agent_id, observed_route=observed)
        except Exception:
            await client.aclose()
            self._active_client = None
            raise

    async def send_and_wait(
        self,
        *,
        api_key: str,
        agent_id: str,
        prompt: str,
        write_policy: WritePolicy,
        timeout_seconds: float | None,
    ) -> SdkRunOutcome:
        from cursor_sdk.asyncio import AsyncAgent

        client = getattr(self, "_active_client", None)
        if client is None:
            raise RuntimeError("create_local_agent must precede send_and_wait on live client")

        agent = AsyncAgent(
            client=client,
            agent_id=agent_id,
            model=None,
            api_key=api_key,
        )
        send_options: dict[str, Any] = {}
        tools = write_policy_tools(write_policy)
        if tools is not None:
            send_options["tools"] = tools

        async def _run() -> SdkRunOutcome:
            run = await agent.send(prompt, send_options or None)
            sdk_status_message: str | None = None
            async for event in run:
                sdk_message = getattr(event, "sdk_message", None)
                if sdk_message is None:
                    continue
                if getattr(sdk_message, "type", None) != "status":
                    continue
                if str(getattr(sdk_message, "status", "")).upper() == "ERROR":
                    msg = getattr(sdk_message, "message", None)
                    if msg:
                        sdk_status_message = str(msg)
            result = await run.wait()
            observed = model_route_from_sdk_model(
                result.model.to_json() if result.model and hasattr(result.model, "to_json") else None
            )
            return SdkRunOutcome(
                run_id=result.id,
                agent_id=result.agent_id or agent_id,
                sdk_status=str(result.status),
                result_text=(result.result or "").strip(),
                observed_route=observed,
                duration_ms=result.duration_ms or None,
                sdk_status_message=sdk_status_message,
            )

        try:
            if timeout_seconds is not None:
                return await asyncio.wait_for(_run(), timeout=timeout_seconds)
            return await _run()
        finally:
            await client.aclose()
            self._active_client = None

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
        from cursor_sdk.asyncio import AsyncAgent

        client = await self._with_workspace_client(workspace)
        try:
            resume_options: dict[str, Any] = {
                "apiKey": api_key,
                "local": agent_local_options(agent_cwd=workspace),
            }
            if model_route is not None:
                resume_options["model"] = model_route_to_sdk_selection(model_route)
            agent = await AsyncAgent.resume(
                agent_id,
                resume_options,
                client=client,
            )
            send_options: dict[str, Any] = {
                "local": agent_local_options(agent_cwd=workspace),
            }
            tools = write_policy_tools(write_policy)
            if tools is not None:
                send_options["tools"] = tools

            async def _run() -> SdkRunOutcome:
                run = await agent.send(prompt, send_options or None)
                sdk_status_message: str | None = None
                async for event in run:
                    sdk_message = getattr(event, "sdk_message", None)
                    if sdk_message is None:
                        continue
                    if getattr(sdk_message, "type", None) != "status":
                        continue
                    if str(getattr(sdk_message, "status", "")).upper() == "ERROR":
                        msg = getattr(sdk_message, "message", None)
                        if msg:
                            sdk_status_message = str(msg)
                result = await run.wait()
                observed = model_route_from_sdk_model(
                    result.model.to_json() if result.model and hasattr(result.model, "to_json") else None
                )
                return SdkRunOutcome(
                    run_id=result.id,
                    agent_id=result.agent_id or agent_id,
                    sdk_status=str(result.status),
                    result_text=(result.result or "").strip(),
                    observed_route=observed,
                    duration_ms=result.duration_ms or None,
                    sdk_status_message=sdk_status_message,
                )

            if timeout_seconds is not None:
                return await asyncio.wait_for(_run(), timeout=timeout_seconds)
            return await _run()
        finally:
            await client.aclose()
            self._active_client = None

    async def get_run_outcome(
        self,
        *,
        api_key: str,
        workspace: str,
        run_id: str,
        agent_id: str | None,
        timeout_seconds: float | None,
    ) -> SdkRunOutcome | None:
        from cursor_sdk.asyncio import AsyncClient

        client = await AsyncClient.launch_bridge(**bridge_launch_kwargs(agent_cwd=workspace))
        try:
            options = {"runtime": "local", "cwd": workspace, "apiKey": api_key}
            run = await client.get_run(run_id, options)

            async def _wait() -> SdkRunOutcome:
                result = await run.wait()
                observed = model_route_from_sdk_model(
                    result.model.to_json() if result.model and hasattr(result.model, "to_json") else None
                )
                return SdkRunOutcome(
                    run_id=result.id,
                    agent_id=result.agent_id or agent_id or "",
                    sdk_status=str(result.status),
                    result_text=(result.result or "").strip(),
                    observed_route=observed,
                    duration_ms=result.duration_ms or None,
                )

            if timeout_seconds is not None:
                return await asyncio.wait_for(_wait(), timeout=timeout_seconds)
            return await _wait()
        except asyncio.TimeoutError:
            return None
        except OSError:
            return None
        except Exception:
            return None
        finally:
            await client.aclose()
