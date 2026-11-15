"""Разрешение source_id и lossless paging для ``read_source`` apply-design hop."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from agents.capabilities.manifest import AgentCapabilityManifest
from agents.capabilities.path_policy import path_denied, path_in_domain, resolve_domain_path
from agents.implementation.apply_design.capability import (
    PLANNING_SOURCE_ALIASES,
    file_digest_bytes,
    planning_source_path,
)
from workflows.artifact_paths import apply_design_artifact_path

READ_SOURCE_PAGE_BYTES = 65_536


class ReadSourceFailureCode(str, Enum):
    """Коды отказа ``read_source`` (не смешивать семантику)."""

    INVALID_ARGS = "INVALID_ARGS"
    UNKNOWN_SOURCE = "UNKNOWN_SOURCE"
    MISSING_FILE = "MISSING_FILE"
    FORBIDDEN_PATH = "FORBIDDEN_PATH"
    SOURCE_REVISION_CONFLICT = "SOURCE_REVISION_CONFLICT"
    READ_FAILURE = "READ_FAILURE"


@dataclass(frozen=True, slots=True)
class ResolvedSource:
    """Успешное разрешение source_id в файл на диске."""

    source_id: str
    path: Path


@dataclass(frozen=True, slots=True)
class ReadSourceFailure:
    """Структурированный отказ чтения."""

    code: ReadSourceFailureCode


def failure_payload(failure: ReadSourceFailure) -> dict[str, Any]:
    """Сериализовать отказ для callback модели."""
    return {"ok": False, "code": failure.code.value}


def classify_domain_source(
    source_id: str,
    repo: Path,
) -> ResolvedSource | ReadSourceFailure:
    """Разрешить ``domain:rel`` вне manifest.readable_sources."""
    if ":" not in source_id:
        return ReadSourceFailure(ReadSourceFailureCode.UNKNOWN_SOURCE)
    domain, _rel = source_id.split(":", 1)
    domain = domain.strip()
    if domain == "planning":
        alias = source_id.strip()
        if alias not in PLANNING_SOURCE_ALIASES:
            return ReadSourceFailure(ReadSourceFailureCode.UNKNOWN_SOURCE)
        return ReadSourceFailure(ReadSourceFailureCode.MISSING_FILE)
    allowed = {
        "product",
        "tests",
        "migrations",
        "docs",
        "changes",
        "specs",
        "decisions",
    }
    if domain not in allowed:
        return ReadSourceFailure(ReadSourceFailureCode.UNKNOWN_SOURCE)
    rel = _rel.strip().replace("\\", "/").lstrip("/")
    if path_denied(rel) or not path_in_domain(rel, domain, repo):
        return ReadSourceFailure(ReadSourceFailureCode.FORBIDDEN_PATH)
    resolved = resolve_domain_path(source_id, repo)
    if resolved is None:
        return ReadSourceFailure(ReadSourceFailureCode.FORBIDDEN_PATH)
    _domain, path = resolved
    if not path.is_file():
        return ReadSourceFailure(ReadSourceFailureCode.MISSING_FILE)
    return ResolvedSource(source_id=source_id, path=path)


def resolve_read_source(
    manifest: AgentCapabilityManifest,
    source_id: str,
) -> ResolvedSource | ReadSourceFailure:
    """Сопоставить ``source_id`` hop manifest с одним файлом или отказом."""
    sid = source_id.strip()
    if not sid:
        return ReadSourceFailure(ReadSourceFailureCode.INVALID_ARGS)
    repo = manifest.repo_root.resolve()

    if sid in manifest.prior_designs:
        desc = manifest.prior_designs[sid]
        path = apply_design_artifact_path(repo, desc.change_id)
        if not path.is_file():
            return ReadSourceFailure(ReadSourceFailureCode.MISSING_FILE)
        return ResolvedSource(source_id=sid, path=path)

    if sid in PLANNING_SOURCE_ALIASES:
        plan_path = planning_source_path(repo, sid, manifest.change_id)
        if plan_path is None or not plan_path.is_file():
            return ReadSourceFailure(ReadSourceFailureCode.MISSING_FILE)
        return ResolvedSource(source_id=sid, path=plan_path)

    if sid in manifest.readable_sources:
        resolved = resolve_domain_path(sid, repo)
        if resolved is None:
            return ReadSourceFailure(ReadSourceFailureCode.FORBIDDEN_PATH)
        _domain, path = resolved
        if not path.is_file():
            return ReadSourceFailure(ReadSourceFailureCode.MISSING_FILE)
        return ResolvedSource(source_id=sid, path=path)

    if ":" not in sid:
        return ReadSourceFailure(ReadSourceFailureCode.UNKNOWN_SOURCE)

    return classify_domain_source(sid, repo)


def _utf8_page(data: bytes, offset: int, page_bytes: int) -> tuple[str, int]:
    """Взять следующий фрагмент UTF-8 без разрыва code point."""
    if offset >= len(data):
        return "", offset
    chunk = data[offset : offset + page_bytes]
    while chunk:
        try:
            text = chunk.decode("utf-8")
            return text, offset + len(chunk)
        except UnicodeDecodeError:
            chunk = chunk[:-1]
    return "", offset


def build_paged_read_ok(
    *,
    source_id: str,
    data: bytes,
    offset: int,
    page_bytes: int,
) -> dict[str, Any]:
    """Собрать успешный lossless page payload."""
    total = len(data)
    revision = file_digest_bytes(data)
    if total == 0:
        return {
            "ok": True,
            "source_id": source_id,
            "source_revision": revision,
            "total_size": 0,
            "offset": 0,
            "fragment": "",
            "next_cursor": None,
            "eof": True,
        }
    if offset < 0 or offset > total:
        offset = 0
    fragment, next_off = _utf8_page(data, offset, page_bytes)
    at_eof = next_off >= total
    return {
        "ok": True,
        "source_id": source_id,
        "source_revision": revision,
        "total_size": total,
        "offset": offset,
        "fragment": fragment,
        "next_cursor": None if at_eof else next_off,
        "eof": at_eof,
    }


async def execute_read_source(
    manifest: AgentCapabilityManifest,
    args: Mapping[str, Any],
    *,
    page_bytes: int | None = None,
) -> dict[str, Any]:
    """Прочитать источник с paging; одна revision на всю цепочку cursor."""
    effective_page = page_bytes if page_bytes is not None else READ_SOURCE_PAGE_BYTES
    source_id = str(args.get("source_id") or "").strip()
    resolved = resolve_read_source(manifest, source_id)
    if isinstance(resolved, ReadSourceFailure):
        return failure_payload(resolved)

    cursor_raw = args.get("cursor", 0)
    try:
        cursor = int(cursor_raw)
    except (TypeError, ValueError):
        return failure_payload(ReadSourceFailure(ReadSourceFailureCode.INVALID_ARGS))
    if cursor < 0:
        return failure_payload(ReadSourceFailure(ReadSourceFailureCode.INVALID_ARGS))

    expected_revision = str(args.get("expected_revision") or "").strip()

    try:
        data = await asyncio.to_thread(resolved.path.read_bytes)
    except OSError:
        return failure_payload(ReadSourceFailure(ReadSourceFailureCode.READ_FAILURE))

    revision = file_digest_bytes(data)
    if cursor > 0 and expected_revision and expected_revision != revision:
        return failure_payload(ReadSourceFailure(ReadSourceFailureCode.SOURCE_REVISION_CONFLICT))
    if cursor > 0 and not expected_revision:
        return failure_payload(ReadSourceFailure(ReadSourceFailureCode.INVALID_ARGS))

    return build_paged_read_ok(
        source_id=resolved.source_id,
        data=data,
        offset=cursor,
        page_bytes=effective_page,
    )
