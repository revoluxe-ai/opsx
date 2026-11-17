"""META allow/deny для правок супервизорного fix-агента (контроллер, не модель)."""

from __future__ import annotations

import fnmatch
import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path


META_WRITE_GLOBS: tuple[str, ...] = (
    "opsx/opsx_runtime/**",
    "opsx/opsx_tools/**",
    "opsx_tools/**",
    "opsx/contract_prompts/**",
    "opsx/configs/**",
    "opsx_cli/**",
    ".cursor/rules/**",
    ".cursor/skills/**",
)

META_DENY_GLOBS: tuple[str, ...] = (
    "opsx_cli/supervisor*.py",
    "opsx/opsx_runtime/agents/process_repair/**",
    "opsx/contract_prompts/process_supervisor/**",
    "tests/test_supervisor.py",
    "opsx/opsx_runtime/workflows/verify/cli_outcome.py",
)

RUN_OPSX_PYTEST_PREFIX = "opsx/core/opsx/opsx_runtime/"


@dataclass(frozen=True, slots=True)
class FileFingerprint:
    """SHA256 рабочего файла для журнала и commit-gate."""

    repo: str
    path: str
    sha256: str


def _repo_relative(path: Path, roots: list[tuple[str, Path]]) -> tuple[str, str] | None:
    resolved = path.resolve()
    for name, root in roots:
        try:
            rel = resolved.relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
        return name, rel
    return None


def git_dirty_fingerprints(project_root: Path, core_root: Path) -> dict[str, FileFingerprint]:
    """Снимок dirty-файлов project root и core submodule."""
    roots = [("project", project_root), ("core", core_root)]
    fingerprints: dict[str, FileFingerprint] = {}

    def scan_repo(name: str, root: Path) -> None:
        if not (root / ".git").exists() and name == "project":
            return
        try:
            out = subprocess.run(
                ["git", "status", "--porcelain", "-u"],
                cwd=root,
                text=True,
                capture_output=True,
                check=True,
            ).stdout
        except (OSError, subprocess.CalledProcessError):
            return
        for line in out.splitlines():
            if len(line) < 4:
                continue
            rel = line[3:].strip().replace("\\", "/")
            if " -> " in rel:
                rel = rel.split(" -> ", 1)[1].strip()
            file_path = root / rel
            if not file_path.is_file():
                continue
            try:
                data = file_path.read_bytes()
            except OSError:
                continue
            key = f"{name}:{rel}"
            fingerprints[key] = FileFingerprint(
                repo=name,
                path=rel,
                sha256=hashlib.sha256(data).hexdigest(),
            )

    for name, root in roots:
        if root.is_dir():
            scan_repo(name, root)
    return fingerprints


def diff_fingerprints(
    before: dict[str, FileFingerprint],
    after: dict[str, FileFingerprint],
) -> list[FileFingerprint]:
    """Файлы, появившиеся или изменившиеся между снимками."""
    changed: list[FileFingerprint] = []
    for key, fp in after.items():
        if key not in before or before[key].sha256 != fp.sha256:
            changed.append(fp)
    return changed


def select_fix_commit_files(
    fix_result: dict,
    before: dict[str, FileFingerprint],
    after: dict[str, FileFingerprint],
) -> tuple[list[FileFingerprint], str | None]:
    """Сузить commit до путей из ``fix_report.files_changed``.

    Игнорирует посторонние dirty-файлы в worktree, не перечисленные в отчёте fix-hop.
    """
    verdict = str(fix_result.get("verdict") or "").upper()
    declared = list(fix_result.get("files_changed") or [])
    if verdict != "FIXED":
        return diff_fingerprints(before, after), None
    if not declared:
        return [], "fix report has no files_changed"
    from_report = fingerprints_for_fix_report_paths(fix_result, after)
    if not from_report:
        return [], "fix report paths not found on disk"
    return from_report, None


def fingerprints_for_fix_report_paths(
    fix_result: dict,
    fingerprints: dict[str, FileFingerprint],
) -> list[FileFingerprint]:
    """Собрать отпечатки файлов из ``fix_report.files_changed``, если diff hop пуст.

    Нужно, когда предыдущий раунд уже записал правки на диск, а git diff
    текущего hop нулевой — commit-gate всё равно должен знать, что коммитить.
    """
    found: list[FileFingerprint] = []
    seen: set[str] = set()
    for raw in fix_result.get("files_changed") or []:
        text = str(raw).replace("\\", "/").strip()
        if not text:
            continue
        candidates: list[str] = []
        if text.startswith("opsx/core/"):
            candidates.append(f"project:{text}")
            candidates.append(f"core:{text[len('opsx/core/') :]}")
        elif text.startswith("opsx/"):
            candidates.append(f"core:{text}")
            candidates.append(f"project:opsx/core/{text}")
        else:
            candidates.append(f"core:{text}")
            candidates.append(f"project:opsx/core/{text}")
        for key in candidates:
            if key in seen:
                continue
            fp = fingerprints.get(key)
            if fp is not None:
                seen.add(key)
                found.append(fp)
                break
    return found


def _meta_write_globs_live() -> tuple[str, ...]:
    """Текущий allowlist: константа модуля (перечитывается после importlib.reload)."""
    return META_WRITE_GLOBS


def _glob_match(rel_posix: str, globs: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(rel_posix, pattern) for pattern in globs)


def _meta_rel_path(fp: FileFingerprint) -> tuple[str | None, str | None]:
    """Путь файла относительно ``core/`` для allow/deny glob-ов META."""
    if fp.repo == "project":
        rel = fp.path
        prefix = "opsx/core/"
        if not rel.startswith(prefix):
            return None, f"product path outside core: {rel}"
        return rel[len(prefix) :], None
    if fp.repo == "core":
        return fp.path, None
    return None, f"unknown repo tag: {fp.repo}"


def validate_meta_write_paths(
    files: list[FileFingerprint],
    *,
    dirty_at_start: set[str],
    core_root: Path,
) -> tuple[bool, str]:
    """Проверить, что изменения только в META allowlist и не в deny."""
    # Перечитать allowlist с диска (длинноживущая панель могла стартовать до расширения globs).
    import importlib

    mod = importlib.import_module(__name__)
    importlib.reload(mod)
    allow = mod._meta_write_globs_live()
    deny = mod.META_DENY_GLOBS
    for fp in files:
        rel, err = _meta_rel_path(fp)
        if err:
            return False, err
        assert rel is not None
        dirty_key = f"{fp.repo}:{fp.path}"
        if dirty_key in dirty_at_start:
            # Грязные до старта run файлы в META allowlist допустимо
            # коммитить вместе с fix (типично — незакоммиченный scaffold из прошлого hop).
            if _glob_match(rel, deny) or not _glob_match(rel, allow):
                return False, f"touching pre-existing dirty file: {dirty_key}"
        if _glob_match(rel, deny):
            return False, f"deny path: {rel}"
        if not _glob_match(rel, allow):
            return False, f"outside META allowlist: {rel}"
        _ = core_root  # reserved for future symlink checks
    return True, ""


def normalize_opsx_pytest_node_id(node_id: str) -> str:
    """Нормализовать относительный путь теста под ``opsx/opsx_runtime`` (без ``::``)."""
    text = node_id.strip().replace("\\", "/")
    if "::" in text:
        text = text.split("::", 1)[0]
    if text.startswith(RUN_OPSX_PYTEST_PREFIX):
        suffix = text.removeprefix(RUN_OPSX_PYTEST_PREFIX)
        return f"opsx/opsx_runtime/{suffix}"
    if text.startswith("opsx/opsx_runtime/"):
        return text
    if text.startswith("tests/"):
        return f"opsx/opsx_runtime/{text.removeprefix('tests/')}"
    raise ValueError("node_id must reference opsx/opsx_runtime tests")


def split_opsx_pytest_node_id(node_id: str) -> tuple[str, str | None]:
    """Разобрать node id fix-отчёта: rel-path под ``core/`` и суффикс ``::test`` при наличии."""
    text = node_id.strip().replace("\\", "/")
    if "::" in text:
        file_part, test_name = text.split("::", 1)
        return normalize_opsx_pytest_node_id(file_part), test_name.strip() or None
    return normalize_opsx_pytest_node_id(text), None


def resolve_opsx_pytest_target(core_root: Path, node_id: str) -> Path:
    """Абсолютный путь к файлу теста под checkout ``opsx/core``."""
    rel, _ = split_opsx_pytest_node_id(node_id)
    return core_root / rel


def format_opsx_pytest_arg(core_root: Path, node_id: str) -> str:
    """Собрать argv для ``rerun_fix_checks``: один файл или ``абс_path::test_name``."""
    rel, test_name = split_opsx_pytest_node_id(node_id)
    path = core_root / rel
    if test_name:
        return f"{path}::{test_name}"
    return str(path)