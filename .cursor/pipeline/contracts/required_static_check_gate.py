"""GATE_INTEGRITY: change-required paths must not hide under prep-green static suppressions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RequiredStaticCheckVerdict:
    """Row-level verdict for scenario audit inverse gate."""

    change_path: str
    row_verdict: str
    gap_class: str | None
    mechanism: str | None = None


def _norm_rel(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def pyright_file_is_ignored(repo_root: Path, rel_path: str) -> bool:
    """True if ``rel_path`` is listed in pyrightconfig_strict.json ``ignore``."""

    cfg_path = repo_root / "pyrightconfig_strict.json"
    if not cfg_path.is_file():
        return False
    data = json.loads(cfg_path.read_text(encoding="utf-8"))
    rel = _norm_rel(rel_path)
    for entry in data.get("ignore", []) or []:
        pat = _norm_rel(str(entry))
        if rel == pat:
            return True
        if pat.endswith("/**") and rel.startswith(pat[: -len("/**")]):
            return True
        if pat.endswith("**") and rel.startswith(pat.rstrip("*")):
            return True
    return False


def mypy_module_is_ignore_errors(repo_root: Path, module: str) -> bool:
    """True if module has ``ignore_errors = true`` in pyproject mypy overrides."""

    pyproject = repo_root / "pyproject.toml"
    if not pyproject.is_file():
        return False
    text = pyproject.read_text(encoding="utf-8")
    mod = module.replace("/", ".").removesuffix(".py")
    blocks = re.split(r"\[\[tool\.mypy\.overrides\]\]", text)
    for block in blocks[1:]:
        mod_m = re.search(r'module\s*=\s*["\']([^"\']+)["\']', block)
        if not mod_m or mod_m.group(1) != mod:
            continue
        if re.search(r"ignore_errors\s*=\s*true", block):
            return True
    return False


def _path_to_mypy_module(rel_path: str) -> str:
    p = _norm_rel(rel_path)
    if p.endswith(".py"):
        p = p[: -len(".py")]
    return p.replace("/", ".").removeprefix("src.")


def audit_required_static_checks(
    repo_root: Path,
    change_required_paths: list[str],
) -> list[RequiredStaticCheckVerdict]:
    """Fail when change-required file/module is suppressed by prep-green static disables."""

    verdicts: list[RequiredStaticCheckVerdict] = []
    for raw in change_required_paths:
        rel = _norm_rel(raw)
        if not rel:
            continue
        if pyright_file_is_ignored(repo_root, rel):
            verdicts.append(
                RequiredStaticCheckVerdict(
                    change_path=rel,
                    row_verdict="FAIL",
                    gap_class="REQUIRED_STATIC_CHECK_DISABLED",
                    mechanism="pyright_ignore",
                )
            )
            continue
        mod = _path_to_mypy_module(rel)
        if mod and mypy_module_is_ignore_errors(repo_root, mod):
            verdicts.append(
                RequiredStaticCheckVerdict(
                    change_path=rel,
                    row_verdict="FAIL",
                    gap_class="REQUIRED_STATIC_CHECK_DISABLED",
                    mechanism="mypy_ignore_errors",
                )
            )
    return verdicts
