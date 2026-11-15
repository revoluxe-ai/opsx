"""DI-контейнер общих hop-агентов: apply, commit и соседние фабрики.

Единая точка для mutating apply/commit subgraph; composition root подключает
``adapter`` и встраивает контейнер в ``VerifyHopContainer.common``.
"""

from __future__ import annotations

from dependency_injector import containers, providers

from bug_log.null_journal import NullBugJournal
from check_workflow_receipt import CheckWorkflowReceipt

from .agents import ApplyAgent, CommitAgent


class CommonAgentsContainer(containers.DeclarativeContainer):
    """Фабрики apply и commit hop-агентов на одном адаптере."""

    adapter = providers.Dependency()
    receipt = providers.Callable(CheckWorkflowReceipt.none)
    bug_journal = providers.Object(NullBugJournal())

    apply_agent = providers.Factory(
        ApplyAgent,
        adapter=adapter,
        receipt=receipt,
    )
    commit_agent = providers.Factory(
        CommitAgent,
        adapter=adapter,
        receipt=receipt,
    )
