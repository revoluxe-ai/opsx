"""DI-контейнер журнала багов."""

from __future__ import annotations

from dependency_injector import containers, providers

from bug_log.null_journal import NullBugJournal


class BugLogContainer(containers.DeclarativeContainer):
    """Провайдер журнала. Live composition root подменяет на FileBugJournal."""

    bug_journal = providers.Object(NullBugJournal())
