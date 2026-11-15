"""SDK-backed CursorAdapter (canonical transport for Temporal lane activities)."""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path
from typing import Any, Callable

from cursor_adapter import (
    CursorAdapter,
    CursorAdapterError,
    CursorTransientError,
    PromptInvocation,
)
from cursor_execution_activity import _default_ledger_root
from model_config import AgentModelConfig

_GAME_ROOT = Path(__file__).resolve().parents[2]
_EXECUTOR_ROOT = _GAME_ROOT / "tools" / "cursor_executor"
if str(_EXECUTOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_EXECUTOR_ROOT))

from cursor_executor.execution_identity import (  # noqa: E402
    bootstrap_execution_id,
    execution_id_for_prompt,
)
from cursor_executor.executor import CursorExecutor  # noqa: E402
from cursor_executor.ledger import LedgerStore  # noqa: E402
from cursor_executor.schemas import (  # noqa: E402
    AdapterTerminalState,
    CursorExecutionRequest,
    RuntimeKind,
    WritePolicy,
)
from parsers import is_transient_error  # noqa: E402
from sdk_model_route import resolve_sdk_model_route, write_policy_for_step  # noqa: E402

_EXECUTOR_FACTORY: Callable[[], CursorExecutor] | None = None
_LEDGER_ROOT: Path | None = None


def configure_sdk_cursor_adapter(
    *,
    executor_factory: Callable[[], CursorExecutor] | None = None,
    ledger_root: Path | str | None = None,
) -> None:
    global _EXECUTOR_FACTORY, _LEDGER_ROOT
    _EXECUTOR_FACTORY = executor_factory
    _LEDGER_ROOT = Path(ledger_root) if ledger_root else None


def reset_sdk_cursor_adapter_config() -> None:
    configure_sdk_cursor_adapter(executor_factory=None, ledger_root=None)


def _ledger_root() -> Path:
    if _LEDGER_ROOT is not None:
        return _LEDGER_ROOT
    return _default_ledger_root()


def _get_executor() -> CursorExecutor:
    if _EXECUTOR_FACTORY is not None:
        return _EXECUTOR_FACTORY()
    return CursorExecutor(ledger=LedgerStore(root=_ledger_root()))


def _run_async(coro):
    return asyncio.run(coro)


class SdkCursorAdapter(CursorAdapter):
    """CursorExecutor transport with multi-turn agent continuity (agent_id session)."""

    def __init__(
        self,
        repo_path: str | Path,
        session_id: str | None = None,
        *,
        workflow_key: str,
        model_config: AgentModelConfig | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        self.repo_path = str(Path(repo_path).resolve())
        self.workflow_key = workflow_key
        self.model_config = model_config
        self.timeout_seconds = timeout_seconds
        self.history: list[PromptInvocation] = []
        if session_id and session_id.startswith("agent-"):
            self._agent_id = session_id
            self._session_epoch = f"restored:{session_id}"
        elif session_id:
            self._session_epoch = session_id
            self._agent_id = None
        else:
            self._session_epoch = str(uuid.uuid4())
            self._agent_id = None
        self._last_bootstrap_timings_ms: dict[str, int] = {}

    def session_id(self) -> str:
        return self._agent_id or self._session_epoch

    def reset_session(self, *, reason: str | None = None) -> str:
        self._agent_id = None
        suffix = reason or "reset"
        self._session_epoch = f"{self.workflow_key}:{suffix}:{uuid.uuid4().hex[:8]}"
        return self.session_id()

    def _catalog_for_authority(self) -> list[Any] | None:
        """Каталог SDK для grok-маршрута. Composer его не требует."""

        executor = _get_executor()
        sdk = getattr(executor, "sdk", None)
        from cursor_executor.auth import resolve_auth

        auth = resolve_auth()
        from cursor_executor.preflight import _catalog_entry_to_wire

        if sdk is not None and hasattr(sdk, "list_models"):
            listed = _run_async(
                sdk.list_models(api_key=auth.api_key, workspace=self.repo_path)
            )
            return [_catalog_entry_to_wire(item) for item in listed]
        if not auth.api_key:
            return None
        from cursor_executor.sdk_client import AsyncCursorSdkClient

        listed = _run_async(
            AsyncCursorSdkClient().list_models(
                api_key=auth.api_key,
                workspace=self.repo_path,
            )
        )
        return [_catalog_entry_to_wire(item) for item in listed]

    def _route(self, step: str | None):
        step_name = step or "review"
        catalog = None
        if self.model_config is not None:
            tier = self.model_config.resolve_step(step_name)
            if tier.model.startswith("grok-4.7"):
                catalog = self._catalog_for_authority()
        return resolve_sdk_model_route(self.model_config, step=step_name, catalog=catalog)

    def last_bootstrap_timings_ms(self) -> dict[str, int]:
        """Durations from the most recent create-only bootstrap (no secrets)."""

        return dict(self._last_bootstrap_timings_ms)

    def bootstrap_agent(self) -> str:
        if self._agent_id:
            return self._agent_id
        executor = _get_executor()
        route = resolve_sdk_model_route(self.model_config, step="review")
        wp = WritePolicy.READ_ONLY
        execution_id = bootstrap_execution_id(
            workflow_key=self.workflow_key,
            session_epoch=self._session_epoch,
        )
        request = CursorExecutionRequest(
            execution_id=execution_id,
            correlation_id=self.workflow_key,
            workspace=self.repo_path,
            runtime=RuntimeKind.LOCAL,
            model_route=route,
            prompt="",
            write_policy=wp,
            timeout_seconds=self.timeout_seconds,
            metadata={"transport": "sdk_cursor_adapter", "phase": "bootstrap"},
        )
        bootstrap = getattr(executor, "bootstrap_local_agent", None)
        if bootstrap is None:
            raise CursorAdapterError("executor missing bootstrap_local_agent")
        try:
            outcome = _run_async(bootstrap(request, execution_id=execution_id))
        except Exception as exc:
            raise CursorAdapterError(f"session bootstrap failed: {exc}") from exc
        agent_id = getattr(outcome, "agent_id", outcome)
        timings = getattr(outcome, "timings_ms", None)
        if timings:
            self._last_bootstrap_timings_ms = dict(timings)
        if not agent_id:
            raise CursorAdapterError("session bootstrap missing agent_id")
        self._agent_id = str(agent_id)
        return self._agent_id

    def execute_prompt(self, prompt: str, *, step: str | None = None) -> PromptInvocation:
        if self._agent_id is None:
            self.bootstrap_agent()
        executor = _get_executor()
        route = resolve_sdk_model_route(self.model_config, step=step)
        wp = WritePolicy(write_policy_for_step(step))
        execution_id = execution_id_for_prompt(
            workflow_key=self.workflow_key,
            session_epoch=self._session_epoch,
            step=step,
            prompt=prompt,
        )
        request = CursorExecutionRequest(
            execution_id=execution_id,
            correlation_id=self.workflow_key,
            workspace=self.repo_path,
            runtime=RuntimeKind.LOCAL,
            model_route=route,
            prompt=prompt,
            write_policy=wp,
            timeout_seconds=self.timeout_seconds,
            metadata={"transport": "sdk_cursor_adapter", "step": step or ""},
        )
        result = _run_async(
            executor.execute_on_existing_agent(request, agent_id=self._agent_id or "")
        )
        if result.agent_id:
            self._agent_id = result.agent_id

        if result.terminal_class == AdapterTerminalState.UNKNOWN:
            raise CursorTransientError(result.error.message if result.error else "execution unknown")
        if result.terminal_class != AdapterTerminalState.SUCCEEDED:
            message = result.error.message if result.error else f"terminal={result.terminal_class.value}"
            if is_transient_error(message):
                raise CursorTransientError(message)
            raise CursorAdapterError(message)

        invocation = PromptInvocation(
            prompt=prompt,
            raw_output=result.result_text or "",
            exit_code=0,
            session_id=self._agent_id or result.agent_id or self._session_epoch,
        )
        self.history.append(invocation)
        return invocation


def make_sdk_cursor_adapter(
    repo_path: str | Path,
    *,
    workflow_key: str,
    session_id: str | None = None,
    model_config: AgentModelConfig | None = None,
    timeout_seconds: int | None = None,
) -> SdkCursorAdapter:
    adapter = SdkCursorAdapter(
        repo_path,
        session_id,
        workflow_key=workflow_key,
        model_config=model_config,
        timeout_seconds=timeout_seconds,
    )
    return adapter
