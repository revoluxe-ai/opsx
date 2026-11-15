"""Граница записи worktree: чужие и заранее существовавшие изменения не поглощаются.

Модуль только сравнивает porcelain/hash. Не вызывает git reset, checkout или clean
и не откатывает изменения Owner.
"""

from __future__ import annotations

from opsx.project_context import resource_path

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path

TERMINAL_WRITE_OWNERSHIP = "BLOCKED_WRITE_OWNERSHIP"
_RECEIPT_PREFIX = "opsx/runs/bug-cases/materialize/"
# Только заранее известный scratch этого runtime. Весь ``.tmp/`` не исключается:
# файл Owner вроде ``.tmp/owner.txt`` остаётся чужим и блокирует hop.
_SCRATCH_PATHS = (
    ".tmp/temporal/git-preflight-latest.md",
)
_SCRATCH_PREFIXES = (
    ".tmp/temporal/locks/",
    # Служебные артефакты этого runtime. Не весь ``opsx/`` и не чужой receipt.
    "opsx/cursor-executor-ledger/",
    "opsx/runs/temporal/config/",
    "opsx/runs/temporal/runs/",
)


@dataclass(frozen=True)
class WorktreeSnapshot:
    """Путь → SHA-256 содержимого (или маркер отсутствия файла)."""

    digests: dict[str, str]


@dataclass(frozen=True)
class WriteOwnershipVerdict:
    blocked: bool
    terminal: str | None
    reason: str


def _git_status(repo: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise OSError(proc.stderr.strip() or "git status failed")
    paths: list[str] = []
    for line in proc.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1].strip()
        if path:
            paths.append(path.replace("\\", "/"))
    return paths


def _digest(repo: Path, rel: str) -> str:
    file_path = resource_path(repo, rel)
    if not file_path.is_file():
        return "MISSING"
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


def capture_worktree(repo_path: str | Path) -> WorktreeSnapshot:
    """Снять грязные пути и хэши. Пустой snapshot — чистое дерево."""

    repo = Path(repo_path)
    digests = {path: _digest(repo, path) for path in _git_status(repo)}
    return WorktreeSnapshot(digests)


def _norm(path: str) -> str:
    return path.replace("\\", "/")


def is_runtime_artifact(path: str) -> bool:
    """True для служебного файла этого runtime, а не для произвольного ``opsx/``.

    Ledger исполнителя, overlay моделей и run-файл Temporal не являются
    продуктовой правкой и не должны останавливать собственный repair.
    """

    norm = _norm(path).lstrip("/")
    if _path_is_unsafe(norm):
        return False
    if norm in _SCRATCH_PATHS:
        return True
    return any(norm.startswith(prefix) for prefix in _SCRATCH_PREFIXES)


def _is_runtime_scratch(path: str) -> bool:
    """Путь, который пишет сам Temporal и который commit-all не должен считать продуктом."""

    return is_runtime_artifact(path)


def _path_is_unsafe(path: str) -> bool:
    """Абсолютный путь, пустая строка, NUL или ``..`` не являются runtime scratch."""

    norm = _norm(path)
    if not norm or norm.startswith("/") or "\x00" in norm:
        return True
    return any(part == ".." for part in norm.split("/"))


def _resolved_relative(repo: Path, path: str) -> str | None:
    """Реальный путь относительно корня репозитория.

    None, если путь лексически опасен или symlink выводит за корень репозитория.
    """

    if _path_is_unsafe(path):
        return None
    root = repo.resolve()
    candidate = root.joinpath(*_norm(path).split("/"))
    try:
        resolved = candidate.resolve()
        relative = resolved.relative_to(root).as_posix()
    except (OSError, RuntimeError, ValueError):
        return None
    if _path_is_unsafe(relative):
        return None
    return relative


def path_escapes_write_scope(
    repo: Path,
    path: str,
    authorized_prefixes: tuple[str, ...],
) -> bool:
    """True, если путь нельзя принять ни как scratch, ни как разрешённую запись.

    Лексический префикс не достаточен: symlink обязан указывать туда же, куда
    разрешает porcelain-путь. ``*`` тестового harness не выпускает путь за
    корень репозитория и не пропускает ``..``.
    """

    if _path_is_unsafe(path):
        return True
    relative = _resolved_relative(repo, path)
    if relative is None:
        return True
    if is_runtime_artifact(path):
        return not is_runtime_artifact(relative)
    if _is_new_receipt(path):
        return not _is_new_receipt(relative)
    if "*" in authorized_prefixes:
        return False
    if _in_scope(path, authorized_prefixes) and not _in_scope(relative, authorized_prefixes):
        return True
    return False


def _is_new_receipt(path: str) -> bool:
    """Новый receipt этого hop. Уже лежавший в дереве receipt чужой и не исключается."""

    return _norm(path).startswith(_RECEIPT_PREFIX)


def _in_scope(path: str, prefixes: tuple[str, ...]) -> bool:
    if _path_is_unsafe(path):
        return False
    if "*" in prefixes:
        return True
    norm = path.replace("\\", "/")
    for prefix in prefixes:
        root = prefix.replace("\\", "/").rstrip("/")
        if not root:
            continue
        if norm == root or norm.startswith(root + "/"):
            return True
    return False


def changed_paths_in_scope(
    repo_path: str | Path,
    baseline: WorktreeSnapshot,
    authorized_prefixes: tuple[str, ...],
) -> list[str]:
    """Пути, чьё содержимое изменилось внутри scope с момента baseline.

    Scratch и новый receipt не считаются продуктовым исходом ремонта.
    """

    current = capture_worktree(repo_path)
    changed: list[str] = []
    for path, digest in current.digests.items():
        if _is_runtime_scratch(path) or _is_new_receipt(path):
            continue
        if baseline.digests.get(path) == digest:
            continue
        if _in_scope(path, authorized_prefixes):
            changed.append(path)
    return sorted(changed)


def worktree_drift_is_authorized_only(
    repo_path: str | Path,
    baseline: WorktreeSnapshot,
    authorized_prefixes: tuple[str, ...],
) -> bool:
    """True, если отличия от baseline — только scratch/receipt или in-scope записи.

    Используется для reconciliation неопределённого repair-hop: внешние пути и
    чужая грязь остаются fail-closed через ``evaluate_write_ownership``.
    """

    verdict = evaluate_write_ownership(
        repo_path,
        baseline=baseline,
        authorized_prefixes=authorized_prefixes,
    )
    if verdict.blocked:
        return False
    try:
        current = capture_worktree(repo_path)
    except OSError:
        return False
    all_paths = set(baseline.digests) | set(current.digests)
    for path in all_paths:
        if baseline.digests.get(path) == current.digests.get(path):
            continue
        if _is_runtime_scratch(path) or _is_new_receipt(path):
            continue
        if not _in_scope(path, authorized_prefixes):
            return False
    return True


def evaluate_write_ownership(
    repo_path: str | Path,
    *,
    baseline: WorktreeSnapshot | None,
    authorized_prefixes: tuple[str, ...],
) -> WriteOwnershipVerdict:
    """Отклонить поглощение чужих или заранее грязных путей.

    Непустое дерево на старте hop блокирует materialize: commit-all забрал бы
    эти пути. Исключение — точный runtime scratch (preflight и locks), не весь
    ``.tmp/``. Новые пути вне ``authorized_prefixes`` тоже блокируют. Новый
    receipt под ``opsx/runs/bug-cases/materialize/`` не считается чужим;
    уже лежавший там файл блокирует. Файлы не удаляются и не откатываются.
    """

    if baseline is None:
        return WriteOwnershipVerdict(
            True,
            TERMINAL_WRITE_OWNERSHIP,
            "write baseline is missing; ownership is unresolved",
        )
    repo = Path(repo_path)
    escapes = [
        path
        for path in baseline.digests
        if path_escapes_write_scope(repo, path, authorized_prefixes)
    ]
    if escapes:
        return WriteOwnershipVerdict(
            True,
            TERMINAL_WRITE_OWNERSHIP,
            "path escapes authorized write scope: " + ",".join(sorted(escapes)),
        )
    preexisting = {
        path: digest
        for path, digest in baseline.digests.items()
        if not _is_runtime_scratch(path)
    }
    if preexisting:
        return WriteOwnershipVerdict(
            True,
            TERMINAL_WRITE_OWNERSHIP,
            "pre-existing dirty paths must not be absorbed: " + ",".join(sorted(preexisting)),
        )
    try:
        current = capture_worktree(repo_path)
    except OSError as exc:
        return WriteOwnershipVerdict(True, TERMINAL_WRITE_OWNERSHIP, f"worktree inspect failed: {exc}")
    foreign = [
        path
        for path in current.digests
        if path_escapes_write_scope(repo, path, authorized_prefixes)
        or (
            path not in baseline.digests
            and not _is_runtime_scratch(path)
            and not _is_new_receipt(path)
            and not _in_scope(path, authorized_prefixes)
        )
    ]
    if foreign:
        return WriteOwnershipVerdict(
            True,
            TERMINAL_WRITE_OWNERSHIP,
            "paths outside authorized write scope: " + ",".join(sorted(foreign)),
        )
    return WriteOwnershipVerdict(False, None, "writes stay inside the authorized scope")
