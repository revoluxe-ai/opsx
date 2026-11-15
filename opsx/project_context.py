"""Separate core resources from project data, explicitly bound by the entrypoint.

No file reads occur during path resolution. A ContextVar isolates simultaneous
project contexts. Legacy tests can still supply a standalone fixture root.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True, slots=True)
class ProjectContext:
    project_root: Path
    core_root: Path


_context: ContextVar[ProjectContext | None] = ContextVar("opsx_project_context", default=None)
_SOURCE_ROOT = Path(__file__).resolve().parent.parent


def bind_project_context(project: Path, core: Path):
    """Bind explicit composition-root paths for this execution context."""
    return _context.set(ProjectContext(project.resolve(), core.resolve()))


def reset_project_context(token) -> None:
    """Restore the previous context after an embedded invocation."""
    _context.reset(token)


def project_root() -> Path:
    """Resolve the explicit execution project; never infer it from core depth."""
    value = _context.get()
    if value is not None:
        return value.project_root
    return Path(os.environ.get("OPSX_PROJECT_ROOT", Path.cwd())).resolve()


def core_root() -> Path:
    """Location of the checked-out shared core."""
    value = _context.get()
    if value is not None:
        return value.core_root
    return Path(os.environ.get("OPSX_CORE_ROOT", _SOURCE_ROOT)).resolve()


def core_resource(repo: Path, *parts: str) -> Path:
    """Use the bound core for the execution project, retaining fixture isolation."""
    candidate = Path(repo).resolve()
    execution = project_root()
    base = core_root() if candidate == execution else candidate
    return base.joinpath(*parts)


_CORE_PREFIXES = (
    "opsx_tools/", ".cursor/pipeline/", ".cursor/commands/", ".cursor/skills/",
    ".cursor/rules/", "opsx/opsx_runtime/", "opsx/di/", "opsx/configs/",
    "opsx/contract_prompts/", "opsx/adopt_instruction/", "opsx/shared/",
    "opsx/policies/", "opsx/schemas/", "opsx/scripts/", "opsx/adapters/",
    "tools/cursor_executor/", "tools/langgraph_runtime/", "tools/openspec/",
    "tools/opsx/", "tools/temporal/", "tools/pyright/",
)


def resource_path(repo: Path, *parts: str | Path) -> Path:
    """Resolve an explicitly requested resource by ownership, without filesystem I/O.

    Product files, project settings, receipts, logs and run data always stay in
    the project. Only the listed distribution resources use the bound core.
    """
    relative = Path(*parts)
    relative_text = relative.as_posix().rstrip("/") + "/"
    context = _context.get()
    execution = context.project_root if context else Path(os.environ.get("OPSX_PROJECT_ROOT", Path.cwd()))
    same_project = os.path.normcase(os.path.abspath(repo)) == os.path.normcase(os.path.abspath(execution))
    if same_project and not relative.is_absolute() and relative_text.startswith(_CORE_PREFIXES):
        base = context.core_root if context else Path(os.environ.get("OPSX_CORE_ROOT", _SOURCE_ROOT))
        return base / relative
    return Path(repo) / relative
