"""Capability manifest: логические id вместо путей в API модели."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


SEARCH_DOMAIN_NAMES = frozenset(
    {
        "product",
        "tests",
        "migrations",
        "docs",
        "changes",
        "specs",
        "decisions",
        "prior_designs",
    }
)


@dataclass(frozen=True)
class ReadDomain:
    """Домен поиска: корни задаёт runtime, не модель."""

    name: str
    roots: tuple[str, ...]


@dataclass(frozen=True)
class SourceDescriptor:
    """Читаемый source по стабильному id."""

    source_id: str
    domain: str


@dataclass(frozen=True)
class PriorDesignDescriptor:
    """Карточка прошлого APPLY-DESIGN в knowledge index."""

    source_id: str
    change_id: str
    summary: str


@dataclass(frozen=True)
class DesignSubmission:
    """Единственная разрешённая запись apply-design для текущего change."""

    change_id: str
    json_path: Path
    md_path: Path
    base_digest: str


@dataclass(frozen=True)
class AgentCapabilityManifest:
    """Контракт одного hop: read domains, sources, prior designs, optional write."""

    change_id: str
    repo_root: Path
    read_domains: Mapping[str, ReadDomain]
    readable_sources: Mapping[str, SourceDescriptor]
    prior_designs: Mapping[str, PriorDesignDescriptor]
    design_submission: DesignSubmission | None = None
    planning_complete: bool = True
    planning_gaps: tuple[str, ...] = field(default_factory=tuple)

    def submission_digest(self) -> str:
        """Digest для ``submit_apply_design.base_digest`` (текущая ревизия на диске)."""
        if self.design_submission is None:
            return ""
        path = self.design_submission.json_path
        if path.is_file():
            return hashlib.sha256(path.read_bytes()).hexdigest()
        return ""

    async def submission_digest_async(self) -> str:
        """Тот же digest, что ``submission_digest``; чтение диска вне event loop."""
        if self.design_submission is None:
            return ""
        path = self.design_submission.json_path
        if not path.is_file():
            return ""
        data = await asyncio.to_thread(path.read_bytes)
        return hashlib.sha256(data).hexdigest()
