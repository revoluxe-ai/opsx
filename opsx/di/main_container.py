"""Корневой DI-контейнер OPSX: фасад composition root verify-impl.

``ApplicationContainer`` — вложенные ``DeclarativeContainer`` на dependency-injector;
``MainContainer`` — стабильный фасад для CLI и тестов.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from dependency_injector import containers, providers

from opsx.di._paths import default_repo_root
from opsx.di._runtime_imports import ensure_opsx_runtime_importable
from opsx.di.containers.adapters_container import AdaptersContainer
from opsx.di.containers.agents_container import AgentsContainer
from opsx.di.containers.bridge_container import BridgeContainer
from opsx.di.containers.ledger_reconcile_container import LedgerReconcileContainer
from opsx.di.containers.verify_paths_container import (
    VerifyPathsContainer,
    _prepare_session_paths,
)
from opsx.di.types import VerifyImplExecuteContext

if TYPE_CHECKING:
    from agents.adapters.cursor_bridge_runtime import CursorBridgeRuntime


@dataclass(slots=True)
class BridgeHandle:
    """Активная сессия bridge после ``init_resources`` (shutdown через ``close_bridge``)."""

    bridge: CursorBridgeRuntime


class ApplicationContainer(containers.DeclarativeContainer):
    """Вложенные категорийные контейнеры verify-impl."""

    config = providers.Configuration()

    bridge = providers.Container(
        BridgeContainer,
        config=config,
        repo_root=providers.Callable(lambda p: p, config.repo_root),
        change_id=config.change_id,
    )
    adapters = providers.Container(
        AdaptersContainer,
        config=config,
        repo_root=providers.Callable(lambda p: p, config.repo_root),
        timeout_seconds=config.timeout_seconds,
    )
    agents = providers.Container(
        AgentsContainer,
        repo_root=providers.Callable(lambda p: p, config.repo_root),
    )
    ledger_reconcile = providers.Container(
        LedgerReconcileContainer,
        repo_root=providers.Callable(lambda p: p, config.repo_root),
        change_id=config.change_id,
    )
    verify_paths = providers.Container(
        VerifyPathsContainer,
        repo_root=providers.Callable(lambda p: p, config.repo_root),
        change_id=config.change_id,
    )


class MainContainer:
    """Фасад wiring ``run_verify_impl.execute``: Resource bridge и ``build_execute_context``."""

    def __init__(
        self,
        change_id: str,
        *,
        repo_root: Path | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        ensure_opsx_runtime_importable(repo_root=repo_root)
        from agents.adapters.cursor_sdk_stream import CURSOR_HOP_HARD_DEADLINE_SECONDS
        from workflows.repo_paths import resolve_opsx_repository_root

        self._change_id = change_id.strip()
        if not self._change_id:
            raise ValueError("VERIFY_IMPL_CHANGE_ID_REQUIRED")
        self.repo_root = resolve_opsx_repository_root(repo_root or default_repo_root())
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else float(CURSOR_HOP_HARD_DEADLINE_SECONDS)
        )
        self._app = ApplicationContainer()
        self._app.config.repo_root.override(self.repo_root)
        self._app.config.change_id.override(self._change_id)
        self._app.config.timeout_seconds.override(self.timeout_seconds)
        self._resources_started = False

    @property
    def change_id(self) -> str:
        """OpenSpec change id текущего прогона."""
        return self._change_id

    @property
    def app(self) -> ApplicationContainer:
        """Низкоуровневый ``DeclarativeContainer`` (провайдеры, wire, override)."""
        return self._app

    @property
    def bridge(self) -> BridgeContainer:
        """Категорийный контейнер bridge (``bridge_runtime`` Resource)."""
        return self._app.bridge()

    async def build_execute_context(
        self,
        bridge: CursorBridgeRuntime,
        *,
        session_id: str | None = None,
    ) -> VerifyImplExecuteContext:
        """Собрать зависимости execute при уже открытом bridge (lifecycle снаружи).

        Типичное использование::

            handle = await container.open_bridge()
            try:
                ctx = await container.build_execute_context(handle.bridge)
                ...
            finally:
                await container.close_bridge(handle)
        """
        session_paths = _prepare_session_paths(
            self.repo_root,
            self._change_id,
            session_id=session_id,
        )
        ledger_outcome = await self._app.ledger_reconcile().startup(
            bridge=bridge,
            session_id=session_paths.session_id,
        )
        adapter_bundle = self._app.adapters().bundle(bridge=bridge)
        verify_hop = self._app.agents().verify_hop(adapters=adapter_bundle)
        gate_repair = self._app.agents().gate_repair_hop(adapters=adapter_bundle)
        implementation_hop = self._app.agents().implementation_hop(adapters=adapter_bundle)
        review_agents = self._app.agents().review(adapters=adapter_bundle)
        initial_state = self._app.verify_paths().initial_state(session_paths=session_paths)
        resume_scope = self._app.verify_paths().resume_scope()
        return VerifyImplExecuteContext(
            bridge=bridge,
            adapters=adapter_bundle,
            verify_hop_agents=verify_hop,
            gate_repair_agents=gate_repair,
            review_agents=review_agents,
            implementation_hop_agents=implementation_hop,
            session_paths=session_paths,
            initial_state=initial_state,
            resume_scope=resume_scope,
            ledger_outcome=ledger_outcome,
        )

    async def open_bridge(self) -> BridgeHandle:
        """Инициализировать Resource bridge (``providers.Resource`` + async generator)."""
        if self._resources_started:
            raise RuntimeError("OPSX_DI_BRIDGE_ALREADY_OPEN")
        await self._app.init_resources()
        self._resources_started = True
        bridge = await self._app.bridge().bridge_runtime()
        return BridgeHandle(bridge=bridge)

    async def close_bridge(self, handle: BridgeHandle | None = None) -> None:
        """Shutdown Resource bridge (аргумент ``handle`` оставлен для совместимости вызова)."""
        _ = handle
        if not self._resources_started:
            return
        await self._app.shutdown_resources()
        self._resources_started = False
