"""Common agents shared by multiple OPSX workflows.

``CommonAgentsContainer`` импортируйте из ``agents.common.container`` — не из
пакета: eager import контейнера тянет apply-hop и ломает загрузку ``contract_prompts``.
"""

from .commit_agent import CommitAgent

__all__ = [
    "CommitAgent",
]

