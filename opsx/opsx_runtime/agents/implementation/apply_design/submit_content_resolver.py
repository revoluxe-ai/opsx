"""Подготовка тела ``submit_apply_design`` без инлайна всего APPLY-DESIGN.json.

Поддерживает директиву ``$from_path`` и серверный пересчёт protected/process
identity через Node (``opsx-apply-pipeline-identity.mjs``), без writer lease.
"""

from __future__ import annotations

from opsx.project_context import resource_path

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from workflows.implementation.node_subprocess import run_subprocess_utf8

_FROM_PATH_KEY = "$from_path"
_RECOMPUTE_IDENTITIES_KEY = "$recompute_identities"

_NODE_PREPARE = r"""
import { readFileSync, writeFileSync } from "node:fs";
const {
  collectProtectedContextPaths,
  hashRepoRelativePaths,
  refreshApplyDesignProtectedContext,
} = await import((await import("node:url")).pathToFileURL((await import("node:path")).resolve(process.env.OPSX_CORE_ROOT || process.cwd(), 'opsx_tools/opsx-apply-pipeline-identity.mjs')).href);

const repo = process.argv[1];
const staging = process.argv[2];
const design = JSON.parse(readFileSync(staging, "utf8"));
if (Number(design.schema_version) < 4) {
  design.schema_version = 4;
}
const protectedPaths = collectProtectedContextPaths(design);
const inspected = design.repository_identity?.inspected_paths || [];
const pathSha256 = hashRepoRelativePaths(repo, [...protectedPaths, ...inspected]);
const refreshed = refreshApplyDesignProtectedContext(design, pathSha256);
writeFileSync(staging, `${JSON.stringify(refreshed.artifact, null, 2)}\n`, "utf8");
"""


@dataclass(frozen=True, slots=True)
class SubmitContentResolution:
    """Результат разрешения ``content`` для submit."""

    ok: bool
    content: str | None = None
    body: dict[str, Any] | None = None
    code: str | None = None


def _failure(code: str) -> SubmitContentResolution:
    return SubmitContentResolution(ok=False, code=code)


def _resolve_from_path(repo: Path, raw_path: str, target: Path) -> Path | None:
    """Путь к JSON внутри репозитория; только канонический APPLY-DESIGN target."""
    rel = raw_path.strip().replace("\\", "/").lstrip("/")
    if not rel:
        return None
    candidate = (resource_path(repo, rel)).resolve()
    try:
        candidate.relative_to(repo)
    except ValueError:
        return None
    target_res = target.resolve()
    if candidate != target_res:
        return None
    if not candidate.is_file():
        return None
    return candidate


def _is_directive_body(body: Mapping[str, Any]) -> bool:
    keys = set(body.keys())
    allowed = {_FROM_PATH_KEY, _RECOMPUTE_IDENTITIES_KEY}
    if not keys.intersection(allowed):
        return False
    non_directive = keys - allowed
    return len(non_directive) == 0


async def _recompute_identities_on_staging(
    *,
    repo: Path,
    staging: Path,
) -> bool:
    """Пересчитать protected/process digest и поднять schema_version на staging."""
    proc = await asyncio.to_thread(
        run_subprocess_utf8,
        ["node", "--input-type=module", "-e", _NODE_PREPARE, str(repo), str(staging)],
        cwd=repo,
        timeout=120,
    )
    return proc.returncode == 0


async def resolve_submit_apply_design_content(
    content: str,
    *,
    repo: Path,
    target: Path,
) -> SubmitContentResolution:
    """Разрешить строку ``content`` в полный JSON для publish pipeline."""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return _failure("INVALID_JSON")
    if not isinstance(parsed, dict):
        return _failure("INVALID_JSON")

    recompute = bool(parsed.get(_RECOMPUTE_IDENTITIES_KEY))
    from_path = parsed.get(_FROM_PATH_KEY)

    if _is_directive_body(parsed):
        if not isinstance(from_path, str) or not from_path.strip():
            return _failure("INVALID_ARGS")
        source = _resolve_from_path(repo, from_path, target)
        if source is None:
            return _failure("FORBIDDEN_PATH")
        body = json.loads(await asyncio.to_thread(source.read_text, encoding="utf-8"))
        if not isinstance(body, dict):
            return _failure("INVALID_JSON")
        recompute = True
    else:
        body = dict(parsed)
        if _FROM_PATH_KEY in body:
            return _failure("INVALID_ARGS")
        if _RECOMPUTE_IDENTITIES_KEY in body:
            body.pop(_RECOMPUTE_IDENTITIES_KEY, None)

    if recompute:
        import os
        import tempfile

        parent = target.parent
        await asyncio.to_thread(parent.mkdir, parents=True, exist_ok=True)
        fd, staging_name = tempfile.mkstemp(
            dir=str(parent),
            prefix=".apply-design-submit-prep-",
            suffix=".json",
        )
        staging = Path(staging_name)
        try:
            await asyncio.to_thread(os.write, fd, json.dumps(body, indent=2).encode("utf-8"))
            await asyncio.to_thread(os.close, fd)
            fd = -1
            if not await _recompute_identities_on_staging(repo=repo, staging=staging):
                return _failure("IDENTITY_RECOMPUTE_FAILED")
            text = await asyncio.to_thread(staging.read_text, encoding="utf-8")
            body = json.loads(text)
        finally:
            if fd >= 0:
                await asyncio.to_thread(os.close, fd)
            if staging.exists():
                await asyncio.to_thread(os.unlink, staging)

    normalized = json.dumps(body, indent=2) + "\n"
    return SubmitContentResolution(ok=True, content=normalized, body=body)
