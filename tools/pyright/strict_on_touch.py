#!/usr/bin/env python3
"""Guard STRICT_ON_TOUCH для ``src/stroy`` (pyright strict).

Сравнивает изменённые относительно cutover-ref ``.py`` файлы (staged, unstaged,
untracked, rename) с baseline legacy-ignore и конфигом ``pyrightconfig_strict.json``.

Правила (fail-closed):

- touched legacy-файл всё ещё в ``ignore`` → FAIL (снять с ignore и пройти strict);
- попытка добавить путь в ``ignore`` сверх baseline → FAIL;
- подмена fingerprint/sha baseline → BLOCKED;
- снятый с ignore touched legacy → обязан пройти pyright strict (audit config).
"""

from __future__ import annotations

from opsx.project_context import resource_path, project_root

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Sequence

_REPO_ROOT = project_root()
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.pyright.baseline import (
    BASELINE_SCHEMA_VERSION,
    BaselineError,
    DEFAULT_BASELINE_REL,
    StrictCutoverBaseline,
    load_baseline,
)
_STRICT_CONFIG_REL = Path("pyrightconfig_strict.json")
_AUDIT_CONFIG_REL = Path("pyrightconfig_strict_audit.json")
_STROY_PREFIX = "src/stroy/"


class GuardStatus(str, Enum):
    """Итог guard."""

    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class GuardFinding:
    """Одно нарушение политики."""

    code: str
    message: str


@dataclass
class GuardResult:
    """Агрегированный результат запуска guard."""

    status: GuardStatus
    findings: tuple[GuardFinding, ...]
    changed_py_files: tuple[str, ...]
    touched_legacy_still_ignored: tuple[str, ...]
    touched_graduated_needing_pyright: tuple[str, ...]


def _run_git(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )


def _normalize_repo_path(path: str) -> str:
    return path.strip().replace("\\", "/")


def collect_changed_py_files(repo_root: Path, cutover_ref: str) -> tuple[str, ...]:
    """Собирает изменённые ``.py`` под ``src/stroy`` от cutover до рабочего дерева."""
    paths: set[str] = set()

    for extra in (
        ["diff", "--name-only", cutover_ref],
        ["diff", "--name-only", "--cached", cutover_ref],
    ):
        proc = _run_git(repo_root, *extra)
        if proc.returncode not in (0, 1):
            stderr = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"git {' '.join(extra)} failed: {stderr}")

        for line in (proc.stdout or "").splitlines():
            rel = _normalize_repo_path(line)
            if rel.endswith(".py") and rel.startswith(_STROY_PREFIX):
                paths.add(rel)

    untracked = _run_git(repo_root, "ls-files", "--others", "--exclude-standard")
    if untracked.returncode != 0:
        stderr = (untracked.stderr or untracked.stdout or "").strip()
        raise RuntimeError(f"git ls-files failed: {stderr}")
    for line in (untracked.stdout or "").splitlines():
        rel = _normalize_repo_path(line)
        if rel.endswith(".py") and rel.startswith(_STROY_PREFIX):
            paths.add(rel)

    return tuple(sorted(paths))


def load_strict_ignore_paths(repo_root: Path) -> tuple[str, ...]:
    """Читает ``ignore`` из ``pyrightconfig_strict.json`` (точные пути, без wildcards)."""
    config_path = resource_path(repo_root, _STRICT_CONFIG_REL)
    if not config_path.is_file():
        raise FileNotFoundError(f"missing {_STRICT_CONFIG_REL}")
    document = json.loads(config_path.read_text(encoding="utf-8"))
    ignore = document.get("ignore", [])
    if ignore is None:
        return ()
    if not isinstance(ignore, list):
        raise ValueError("ignore must be a list in pyrightconfig_strict.json")
    normalized: list[str] = []
    for entry in ignore:
        if not isinstance(entry, str):
            raise ValueError("ignore entries must be strings")
        if any(ch in entry for ch in "*?[]"):
            raise ValueError(f"wildcards forbidden in strict ignore list: {entry}")
        normalized.append(_normalize_repo_path(entry))
    return tuple(sorted(normalized))


def _resolve_pyright_binary(repo_root: Path) -> Path:
    """Ищет pyright в venv репозитория или в корне монорепо guard."""
    for root in (repo_root, _REPO_ROOT):
        candidate = resource_path(root, ".venv", "bin", "pyright")
        if candidate.is_file():
            return candidate
    return Path("pyright")


def pyright_strict_passes_for_files(repo_root: Path, rel_paths: Sequence[str]) -> tuple[bool, str]:
    """Запускает pyright strict (audit config) для указанных файлов."""
    if not rel_paths:
        return True, ""
    pyright = _resolve_pyright_binary(repo_root)

    cmd = [
        str(pyright),
        "--project",
        str(_AUDIT_CONFIG_REL),
        "--outputjson",
        *rel_paths,
    ]
    proc = subprocess.run(
        cmd,
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        return True, ""
    try:
        payload = json.loads(proc.stdout or "{}")
        errors = payload.get("summary", {}).get("errorCount", "?")
        return False, f"pyright errors={errors}"
    except json.JSONDecodeError:
        return False, (proc.stderr or proc.stdout or "pyright failed").strip()[:500]


def evaluate_strict_on_touch(
    repo_root: Path,
    baseline: StrictCutoverBaseline,
    *,
    skip_pyright: bool = False,
) -> GuardResult:
    """Основная логика guard без subprocess CLI."""
    findings: list[GuardFinding] = []
    cutover_ref = baseline.cutover_git_sha
    if not cutover_ref:
        return GuardResult(
            GuardStatus.BLOCKED,
            (GuardFinding("MISSING_CUTOVER_SHA", "baseline cutover git_sha is empty"),),
            (),
            (),
            (),
        )

    head_proc = _run_git(repo_root, "rev-parse", "HEAD")
    if head_proc.returncode != 0:
        return GuardResult(
            GuardStatus.BLOCKED,
            (GuardFinding("GIT_HEAD", "cannot resolve HEAD"),),
            (),
            (),
            (),
        )

    try:
        current_ignore = frozenset(load_strict_ignore_paths(repo_root))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        return GuardResult(
            GuardStatus.BLOCKED,
            (GuardFinding("STRICT_CONFIG", str(error)),),
            (),
            (),
            (),
        )

    baseline_legacy = baseline.legacy_ignored_set
    reignored = sorted(current_ignore - baseline_legacy)
    if reignored:
        sample = ", ".join(reignored[:5])
        more = f" (+{len(reignored) - 5} more)" if len(reignored) > 5 else ""
        findings.append(
            GuardFinding(
                "RE_IGNORE",
                f"ignore list contains paths not in cutover baseline: {sample}{more}",
            )
        )

    try:
        changed = collect_changed_py_files(repo_root, cutover_ref)
    except RuntimeError as error:
        return GuardResult(
            GuardStatus.BLOCKED,
            (GuardFinding("GIT_DIFF", str(error)),),
            (),
            (),
            (),
        )

    touched_legacy_still_ignored: list[str] = []
    touched_graduated: list[str] = []

    for rel in changed:
        if rel in current_ignore:
            touched_legacy_still_ignored.append(rel)
            findings.append(
                GuardFinding(
                    "TOUCHED_LEGACY_IGNORED",
                    f"{rel}: modified while still listed in pyrightconfig_strict.json ignore; "
                    "remove from ignore and fix strict errors",
                )
            )
        elif rel in baseline_legacy:
            touched_graduated.append(rel)

    if touched_graduated and not skip_pyright:
        ok, detail = pyright_strict_passes_for_files(repo_root, touched_graduated)
        if not ok:
            for rel in touched_graduated:
                findings.append(
                    GuardFinding(
                        "GRADUATED_STRICT_FAIL",
                        f"{rel}: removed from ignore but pyright strict failed ({detail})",
                    )
                )

    if findings:
        status = GuardStatus.FAIL
        if any(f.code in {"BASELINE_TAMPERED", "MISSING_BASELINE"} for f in findings):
            status = GuardStatus.BLOCKED
        return GuardResult(
            status,
            tuple(findings),
            changed,
            tuple(sorted(touched_legacy_still_ignored)),
            tuple(sorted(touched_graduated)),
        )

    return GuardResult(
        GuardStatus.PASS,
        (),
        changed,
        (),
        tuple(sorted(touched_graduated)),
    )


def run_guard(
    repo_root: Path,
    *,
    baseline_rel: Path | None = None,
    skip_pyright: bool = False,
) -> GuardResult:
    """Загружает baseline и выполняет guard."""
    try:
        baseline = load_baseline(repo_root, baseline_rel)
    except BaselineError as error:
        return GuardResult(
            GuardStatus.BLOCKED,
            (GuardFinding(error.code, str(error)),),
            (),
            (),
            (),
        )

    if baseline.schema_version != BASELINE_SCHEMA_VERSION:
        return GuardResult(
            GuardStatus.BLOCKED,
            (
                GuardFinding(
                    "UNSUPPORTED_SCHEMA",
                    f"unsupported baseline schema {baseline.schema_version!r}",
                ),
            ),
            (),
            (),
            (),
        )

    return evaluate_strict_on_touch(repo_root, baseline, skip_pyright=skip_pyright)


def _format_result(result: GuardResult) -> str:
    lines = [f"STATUS: {result.status.value}"]
    for finding in result.findings:
        lines.append(f"{finding.code}: {finding.message}")
    if result.changed_py_files:
        lines.append(f"CHANGED_PY: {len(result.changed_py_files)} file(s)")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="STRICT_ON_TOUCH guard for src/stroy pyright strict")
    _ = parser.add_argument(
        "--repo-root",
        type=Path,
        default=_REPO_ROOT,
        help="Корень репозитория (по умолчанию — родитель tools/pyright)",
    )
    _ = parser.add_argument(
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE_REL,
        help="Относительный путь к cutover baseline JSON",
    )
    _ = parser.add_argument(
        "--skip-pyright",
        action="store_true",
        help="Не вызывать pyright для graduated touched файлов (только для unit-тестов)",
    )
    _ = parser.add_argument(
        "--json",
        action="store_true",
        help="Вывести машиночитаемый JSON вместо текста",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    result = run_guard(args.repo_root.resolve(), baseline_rel=args.baseline, skip_pyright=args.skip_pyright)

    if args.json:
        payload = {
            "status": result.status.value,
            "findings": [{"code": f.code, "message": f.message} for f in result.findings],
            "changed_py_files": list(result.changed_py_files),
            "touched_legacy_still_ignored": list(result.touched_legacy_still_ignored),
            "touched_graduated_needing_pyright": list(result.touched_graduated_needing_pyright),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(_format_result(result))

    if result.status == GuardStatus.PASS:
        return 0
    if result.status == GuardStatus.BLOCKED:
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
