# -*- coding: utf-8 -*-
"""Разрешение durable project store для Bug Team case roots (V1).

Политика:
- ``~/opsx/bug-history/<PROJECT_NAME>/cases/<CASE_UUID>/`` (default fleet).
- ``OPSX_BUG_CASE_STORE_ROOT`` — fleet override: ``<fleet>/<PROJECT_NAME>/cases/``.
- PROJECT_NAME из ``.project/PROJECT_PROFILE.yaml`` (``project.name``) или
  явного ``project_name`` в ``opsx/sources.yaml``.
- Новые CASE_ID — UUIDv4; legacy идентификаторы только для resume/read.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from .causal_history.platform_adapter import probe_platform

_DEFAULT_FLEET = Path.home() / "opsx" / "bug-history"
_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_LEGACY_CASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PROJECT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_BINDING_FILE = "opsx/project_name_binding"
_UUID_COLLISION_MAX_ATTEMPTS = 8


class CaseRootMode(str, Enum):
    """Режим разрешения case root."""

    DURABLE_PROJECT_CASE = "DURABLE_PROJECT_CASE"
    SYNTHETIC_TEST_CASE = "SYNTHETIC_TEST_CASE"


class DurableStoreErrorCode(str, Enum):
    """Коды ошибок durable resolver."""

    DURABLE_STORE_UNRESOLVED = "DURABLE_STORE_UNRESOLVED"
    STORE_INSIDE_WORKTREE = "STORE_INSIDE_WORKTREE"
    STORE_UNDER_TMP = "STORE_UNDER_TMP"
    UNSUPPORTED_FILESYSTEM = "UNSUPPORTED_FILESYSTEM"
    INVALID_CASE_ID = "INVALID_CASE_ID"
    PROJECT_NAME_UNRESOLVED = "PROJECT_NAME_UNRESOLVED"
    PROJECT_NAME_BINDING_CONFLICT = "PROJECT_NAME_BINDING_CONFLICT"
    CASE_CREATE_COLLISION = "CASE_CREATE_COLLISION"


class DurableStoreUnresolved(RuntimeError):
    """Durable root не установлен или не прошёл проверки (fail-closed)."""

    def __init__(self, code: DurableStoreErrorCode, detail: str, recovery: str) -> None:
        self.code = code
        self.detail = detail
        self.recovery = recovery
        super().__init__(f"{code.value}: {detail}")


@dataclass(frozen=True)
class ProjectIdentity:
    """Стабильная OPSX project identity для bug history (имя, не git origin)."""

    project_name: str
    repo_family_id: str
    repo_root: Path

    @property
    def project_id(self) -> str:
        """Alias для acceptance/legacy callers (== project_name)."""
        return self.project_name


@dataclass(frozen=True)
class ExecutionBinding:
    """Привязка исполнения к worktree и revision."""

    execution_id: str
    worktree_id: str
    revision_binding: str


@dataclass(frozen=True)
class ResolvedCaseRoot:
    """Результат resolve: путь case + метаданные."""

    case_root: Path
    case_id: str
    finding_id: Optional[str]
    mode: CaseRootMode
    project: ProjectIdentity
    execution: ExecutionBinding
    store_root: Path
    fleet_store_root: Optional[Path]
    synthetic: bool


def _realpath(path: Path) -> Path:
    return Path(os.path.realpath(path))


def _configured_path(path: Path) -> Path:
    """Путь как задан оператором (без follow symlinks) для границ worktree."""
    return path.expanduser().absolute()


def _git_toplevel(repo_root: Path) -> Path:
    out = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=repo_root,
        text=True,
    ).strip()
    return _realpath(Path(out))


def _worktree_id(repo_root: Path) -> str:
    git_dir = subprocess.check_output(
        ["git", "rev-parse", "--git-dir"],
        cwd=repo_root,
        text=True,
    ).strip()
    return hashlib.sha256(git_dir.encode("utf-8")).hexdigest()[:16]


def _revision_binding(repo_root: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        text=True,
    ).strip()


def normalize_remote_url(url: str) -> str:
    """Канонический origin (вспомогательный; не определяет storage path)."""
    raw = url.strip()
    if not raw:
        return ""
    scp = re.match(r"^[^@]+@([^:]+):(.+)$", raw)
    if scp:
        host, path = scp.group(1), scp.group(2).strip("/")
        if path.endswith(".git"):
            path = path[:-4]
        canonical = f"{host.lower()}/{path}"
    else:
        parsed = urlparse(raw if "://" in raw else f"ssh://{raw}")
        host = (parsed.hostname or "").lower()
        path = (parsed.path or "").strip("/")
        if path.endswith(".git"):
            path = path[:-4]
        canonical = f"{host}/{path}" if host else raw.lower()
    return canonical.rstrip("/")


def _yaml_scalar_after_key(text: str, key_path: str) -> Optional[str]:
    """Минимальный reader ``key: value`` / вложенный ``parent:\\n  child:``."""
    parts = key_path.split(".")
    if len(parts) == 1:
        prefix = f"{parts[0]}:"
        for line in text.splitlines():
            if line.startswith(prefix):
                val = line.split(":", 1)[1].strip()
                return val or None
        return None
    parent, child = parts[0], parts[1]
    lines = text.splitlines()
    in_parent = False
    for line in lines:
        if line.startswith(f"{parent}:"):
            in_parent = True
            continue
        if in_parent:
            if line and not line.startswith(" ") and not line.startswith("\t"):
                break
            stripped = line.strip()
            if stripped.startswith(f"{child}:"):
                return stripped.split(":", 1)[1].strip() or None
    return None


def _read_sources_project_name(repo_root: Path) -> Optional[str]:
    path = repo_root / "opsx" / "sources.yaml"
    if not path.is_file():
        return None
    return _yaml_scalar_after_key(path.read_text(encoding="utf-8"), "project_name")


def _read_profile_project_name(repo_root: Path) -> Optional[str]:
    path = repo_root / ".project" / "PROJECT_PROFILE.yaml"
    if not path.is_file():
        return None
    return _yaml_scalar_after_key(path.read_text(encoding="utf-8"), "project.name")


def _read_repo_family_id(repo_root: Path) -> str:
    path = repo_root / "opsx" / "sources.yaml"
    if not path.is_file():
        raise DurableStoreUnresolved(
            DurableStoreErrorCode.PROJECT_NAME_UNRESOLVED,
            f"missing {path}",
            "Add opsx/sources.yaml",
        )
    val = _yaml_scalar_after_key(path.read_text(encoding="utf-8"), "repo_family_id")
    if not val:
        raise DurableStoreUnresolved(
            DurableStoreErrorCode.PROJECT_NAME_UNRESOLVED,
            "repo_family_id not found in sources.yaml",
            "Set repo_family_id in opsx/sources.yaml",
        )
    return val


def _validate_project_name(name: str) -> str:
    if not _PROJECT_NAME_RE.match(name):
        raise DurableStoreUnresolved(
            DurableStoreErrorCode.PROJECT_NAME_UNRESOLVED,
            f"invalid PROJECT_NAME: {name!r}",
            "Use alphanumeric PROJECT_NAME with . _ - (max 64)",
        )
    return name


def _read_explicit_name_binding(repo_root: Path) -> Optional[str]:
    env = os.environ.get("OPSX_PROJECT_NAME_BINDING", "").strip()
    if env:
        return env
    legacy_env = os.environ.get("OPSX_PROJECT_ID_BINDING", "").strip()
    if legacy_env and "@" not in legacy_env:
        return legacy_env
    path = repo_root / _BINDING_FILE
    if path.is_file():
        line = path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
        return line or None
    legacy_path = repo_root / "opsx/project_identity_binding"
    if legacy_path.is_file():
        line = legacy_path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
        if line and "@" not in line:
            return line
    return None


def _enforce_project_name_binding(repo_root: Path, computed_name: str) -> None:
    explicit = _read_explicit_name_binding(repo_root)
    if explicit and explicit != computed_name:
        raise DurableStoreUnresolved(
            DurableStoreErrorCode.PROJECT_NAME_BINDING_CONFLICT,
            f"binding {explicit!r} != configured {computed_name!r}",
            "Fix OPSX_PROJECT_NAME_BINDING / opsx/project_name_binding",
        )


def resolve_project_identity(repo_root: Optional[Path] = None) -> ProjectIdentity:
    """
    PROJECT_NAME для bug history (worktree-stable, без git origin hash).
    """
    root = _realpath(Path(repo_root or Path.cwd()))
    profile_name = _read_profile_project_name(root)
    sources_name = _read_sources_project_name(root)
    if profile_name and sources_name and profile_name != sources_name:
        raise DurableStoreUnresolved(
            DurableStoreErrorCode.PROJECT_NAME_BINDING_CONFLICT,
            f"PROJECT_PROFILE name {profile_name!r} != sources project_name {sources_name!r}",
            "Align project.name and sources.yaml project_name",
        )
    raw_name = profile_name or sources_name
    if not raw_name:
        raise DurableStoreUnresolved(
            DurableStoreErrorCode.PROJECT_NAME_UNRESOLVED,
            "missing project.name or sources.yaml project_name",
            "Set .project/PROJECT_PROFILE.yaml project.name or opsx/sources.yaml project_name",
        )
    project_name = _validate_project_name(raw_name)
    _enforce_project_name_binding(root, project_name)
    family = _read_repo_family_id(root)
    return ProjectIdentity(
        project_name=project_name,
        repo_family_id=family,
        repo_root=root,
    )


def _path_has_tmp_segment(path: Path) -> bool:
    return any(part == ".tmp" for part in path.parts)


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_durable_store_root(
    store_root: Path,
    *,
    repo_root: Path,
) -> None:
    """Проверяет fleet/project store root (realpath, symlink-safe)."""
    configured = _configured_path(store_root)
    root = _realpath(store_root)
    if _path_has_tmp_segment(configured) or _path_has_tmp_segment(root):
        raise DurableStoreUnresolved(
            DurableStoreErrorCode.STORE_UNDER_TMP,
            f"store root under .tmp segment: {configured}",
            "Set OPSX_BUG_CASE_STORE_ROOT outside .tmp directories",
        )
    toplevel = _git_toplevel(repo_root)
    if _is_under(configured, toplevel) or _is_under(root, toplevel):
        raise DurableStoreUnresolved(
            DurableStoreErrorCode.STORE_INSIDE_WORKTREE,
            f"store root {configured} is inside worktree {toplevel}",
            "Use default ~/opsx/bug-history or external OPSX_BUG_CASE_STORE_ROOT",
        )
    fleet_env = os.environ.get("OPSX_BUG_CASE_STORE_ROOT", "").strip()
    if fleet_env and sys.platform == "win32":
        return
    probe = probe_platform()
    if probe.platform_class.value == "UNSUPPORTED":
        raise DurableStoreUnresolved(
            DurableStoreErrorCode.UNSUPPORTED_FILESYSTEM,
            probe.notes,
            "Use Linux ext4 or supported platform for durable causal store",
        )


def resolve_project_store_roots(project: ProjectIdentity) -> tuple[Path, Optional[Path]]:
    """
    Returns (cases_parent_dir, fleet_store_root).

    Layout: ``<fleet>/<PROJECT_NAME>/cases/``
    """
    env = os.environ.get("OPSX_BUG_CASE_STORE_ROOT", "").strip()
    fleet_configured = Path(env).expanduser() if env else _DEFAULT_FLEET
    validate_durable_store_root(fleet_configured, repo_root=project.repo_root)
    fleet = _realpath(fleet_configured)
    project_cases = fleet / project.project_name / "cases"
    validate_durable_store_root(project_cases, repo_root=project.repo_root)
    project_cases.mkdir(parents=True, exist_ok=True)
    return project_cases, fleet if env else None


def resolve_durable_store_root(project: ProjectIdentity) -> Path:
    """Project cases directory (parent of CASE_ID dirs)."""
    project_root, _fleet = resolve_project_store_roots(project)
    return project_root


def validate_case_id(case_id: str, *, for_new_case: bool) -> None:
    """UUIDv4 для новых кейсов; legacy + UUID для resume."""
    if for_new_case:
        if not _UUID4_RE.match(case_id):
            raise DurableStoreUnresolved(
                DurableStoreErrorCode.INVALID_CASE_ID,
                case_id,
                "New cases require UUIDv4 CASE_ID",
            )
        return
    if _UUID4_RE.match(case_id) or _LEGACY_CASE_ID_RE.match(case_id):
        return
    raise DurableStoreUnresolved(
        DurableStoreErrorCode.INVALID_CASE_ID,
        case_id,
        "CASE_ID must be UUIDv4 or legacy alphanumeric id",
    )


def generate_case_uuid() -> str:
    """Криптографически стойкий UUIDv4 для нового bug case."""
    return str(uuid.uuid4())


def _case_root_path(project_store: Path, case_id: str) -> Path:
    root = _realpath(project_store / case_id)
    if not _is_under(root, _realpath(project_store)):
        raise DurableStoreUnresolved(
            DurableStoreErrorCode.DURABLE_STORE_UNRESOLVED,
            f"case path escapes project store: {root}",
            "Invalid CASE_ID path traversal",
        )
    return root


def create_new_durable_project_case(
    *,
    repo_root: Optional[Path] = None,
    finding_id: Optional[str] = None,
    execution_id: Optional[str] = None,
) -> ResolvedCaseRoot:
    """
    Атомарно создаёт новый case (UUIDv4 + exclusive mkdir).
    """
    project = resolve_project_identity(repo_root)
    project_store, fleet = resolve_project_store_roots(project)
    last_err: Optional[Exception] = None
    for _attempt in range(_UUID_COLLISION_MAX_ATTEMPTS):
        case_id = generate_case_uuid()
        validate_case_id(case_id, for_new_case=True)
        case_root = _case_root_path(project_store, case_id)
        assert_not_repo_local_fallback(case_root, project.repo_root)
        try:
            case_root.mkdir(parents=False)
            break
        except FileExistsError as exc:
            last_err = exc
            continue
    else:
        raise DurableStoreUnresolved(
            DurableStoreErrorCode.CASE_CREATE_COLLISION,
            f"uuid directory collision after {_UUID_COLLISION_MAX_ATTEMPTS} attempts",
            "Retry case creation or inspect store for corruption",
        ) from last_err

    exec_id = execution_id or f"EXEC/{uuid.uuid4().hex[:12]}"
    binding = ExecutionBinding(
        execution_id=exec_id,
        worktree_id=_worktree_id(project.repo_root),
        revision_binding=_revision_binding(project.repo_root),
    )
    return ResolvedCaseRoot(
        case_root=case_root,
        case_id=case_id,
        finding_id=finding_id,
        mode=CaseRootMode.DURABLE_PROJECT_CASE,
        project=project,
        execution=binding,
        store_root=project_store,
        fleet_store_root=fleet,
        synthetic=False,
    )


def resolve_durable_project_case(
    case_id: str,
    *,
    repo_root: Optional[Path] = None,
    finding_id: Optional[str] = None,
    execution_id: Optional[str] = None,
    create_if_missing: bool = True,
) -> ResolvedCaseRoot:
    """Открывает существующий durable case (resume); legacy CASE_ID допустим."""
    project = resolve_project_identity(repo_root)
    validate_case_id(case_id, for_new_case=False)
    project_store, fleet = resolve_project_store_roots(project)
    case_root = _case_root_path(project_store, case_id)
    assert_not_repo_local_fallback(case_root, project.repo_root)
    if create_if_missing:
        case_root.mkdir(parents=True, exist_ok=True)
    exec_id = execution_id or f"EXEC/{uuid.uuid4().hex[:12]}"
    binding = ExecutionBinding(
        execution_id=exec_id,
        worktree_id=_worktree_id(project.repo_root),
        revision_binding=_revision_binding(project.repo_root),
    )
    return ResolvedCaseRoot(
        case_root=case_root,
        case_id=case_id,
        finding_id=finding_id,
        mode=CaseRootMode.DURABLE_PROJECT_CASE,
        project=project,
        execution=binding,
        store_root=project_store,
        fleet_store_root=fleet,
        synthetic=False,
    )


def resolve_synthetic_test_case(
    case_id: str,
    synthetic_root: Path,
    *,
    repo_root: Optional[Path] = None,
    finding_id: Optional[str] = None,
) -> ResolvedCaseRoot:
    """SYNTHETIC_TEST_CASE: явный временный root (harness)."""
    root_path = Path(repo_root) if repo_root else Path.cwd()
    if root_path.joinpath("opsx/sources.yaml").is_file():
        try:
            project = resolve_project_identity(repo_root)
        except DurableStoreUnresolved:
            project = ProjectIdentity(
                project_name="synthetic",
                repo_family_id="synthetic",
                repo_root=_realpath(Path(synthetic_root)),
            )
    else:
        project = ProjectIdentity(
            project_name="synthetic",
            repo_family_id="synthetic",
            repo_root=_realpath(Path(synthetic_root)),
        )
    validate_case_id(case_id, for_new_case=False)
    root = _realpath(Path(synthetic_root))
    root.mkdir(parents=True, exist_ok=True)
    case_root = _realpath(root / case_id)
    case_root.mkdir(parents=True, exist_ok=True)
    binding = ExecutionBinding(
        execution_id=f"SYNTH/{uuid.uuid4().hex[:8]}",
        worktree_id="synthetic",
        revision_binding="synthetic",
    )
    return ResolvedCaseRoot(
        case_root=case_root,
        case_id=case_id,
        finding_id=finding_id,
        mode=CaseRootMode.SYNTHETIC_TEST_CASE,
        project=project,
        execution=binding,
        store_root=root,
        fleet_store_root=None,
        synthetic=True,
    )


def assert_not_repo_local_fallback(case_root: Path, repo_root: Path) -> None:
    """Guard: запрещён silent fallback на git_toplevel/opsx/runs/bug-cases."""
    forbidden = _realpath(_git_toplevel(repo_root) / "opsx" / "runs" / "bug-cases")
    resolved = _realpath(case_root)
    if _is_under(resolved, forbidden) or resolved == forbidden:
        raise DurableStoreUnresolved(
            DurableStoreErrorCode.DURABLE_STORE_UNRESOLVED,
            f"repo-local bug-cases path not allowed for durable activation: {case_root}",
            "Use ~/opsx/bug-history or OPSX_BUG_CASE_STORE_ROOT fleet root",
        )
