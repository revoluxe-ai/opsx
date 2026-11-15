"""Bounded dependency/API discovery для Architecture Evidence S2.

Сканирует Change-артефакты, объявленную affected-surface и релевантный код
(imports/call sites) без полного repo scan.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Category = Literal["LIBRARY", "EXTERNAL_API"]

_CHANGE_ARTIFACT_GLOBS = (
    "owner-contract.md",
    "proposal.md",
    "design.md",
    "specs/**/*.md",
)

_AFFECTED_SURFACE_BLOCK = "ARCHITECTURE_AFFECTED_SURFACE"
_PYPROJECT_CANDIDATES = ("pyproject.toml", "poetry.lock")

_CODE_EXTENSIONS = (".py",)

# Явные сигналы интеграции с MAX (не «MAX-class» narrative).
_MAX_INTEGRATION_MARKDOWN = re.compile(
    r"(?:"
    r"MAX\s+messenger\s+API|"
    r"platform-api\.max|"
    r"api\.max\.ru|"
    r"POST\s+/messages.*MAX|"
    r"external\s+API\s+provider:\s*MAX"
    r")",
    re.IGNORECASE,
)

_MAX_INTEGRATION_CODE = re.compile(
    r"(?:"
    r"platform-api\.max|"
    r"api\.max\.ru|"
    r'["\']/messages["\']|'
    r"max\.ru/|"
    r"MaxMessenger|"
    r"max_send_message"
    r")",
    re.IGNORECASE,
)

_HTTPX_CLIENT = re.compile(r"\bhttpx\.(AsyncClient|Client)\b|^\s*import\s+httpx\b", re.M)


@dataclass(frozen=True)
class DiscoveredOperation:
    """Кандидат на coverage obligation из детерминированного discovery."""

    obligation_id: str
    category: Category
    evidence_source: str
    rationale: str


@dataclass
class DiscoveryResult:
    """Итог bounded discovery."""

    operations: list[DiscoveredOperation] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    scanned_paths: list[str] = field(default_factory=list)

    def obligation_ids(self) -> set[str]:
        return {item.obligation_id for item in self.operations}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _collect_change_text(change_root: Path) -> list[tuple[str, str]]:
    chunks: list[tuple[str, str]] = []
    for pattern in _CHANGE_ARTIFACT_GLOBS:
        for path in sorted(change_root.glob(pattern)):
            if path.is_file():
                rel = path.relative_to(change_root).as_posix()
                chunks.append((rel, _read_text(path)))
    return chunks


def _parse_affected_surface_paths(change_root: Path, repo_root: Path) -> list[Path]:
    """Пути из ARCHITECTURE_AFFECTED_SURFACE и proposal Impact hints."""

    paths: list[Path] = []
    design = change_root / "design.md"
    if design.is_file():
        text = _read_text(design)
        lines = text.splitlines()
        in_block = False
        for line in lines:
            stripped = line.strip()
            if stripped == _AFFECTED_SURFACE_BLOCK:
                in_block = True
                continue
            if in_block:
                if stripped.startswith("#") and not stripped.startswith("- "):
                    break
                if stripped.startswith("affected_path:"):
                    rel = stripped.split(":", 1)[1].strip()
                    candidate = (repo_root / rel).resolve()
                    if candidate.exists():
                        paths.append(candidate)
                elif stripped.startswith("- path:"):
                    rel = stripped.split(":", 1)[1].strip()
                    candidate = (repo_root / rel).resolve()
                    if candidate.exists():
                        paths.append(candidate)

    proposal = change_root / "proposal.md"
    if proposal.is_file():
        for match in re.finditer(r"affected_path:\s*([^\s`]+)", _read_text(proposal)):
            candidate = (repo_root / match.group(1).strip()).resolve()
            if candidate.exists():
                paths.append(candidate)

    deduped: dict[str, Path] = {}
    for path in paths:
        deduped[str(path)] = path
    return list(deduped.values())


def _expand_code_files(roots: list[Path], repo_root: Path) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if root.is_file() and root.suffix in _CODE_EXTENSIONS:
            files.append(root)
        elif root.is_dir():
            for ext in _CODE_EXTENSIONS:
                files.extend(sorted(root.rglob(f"*{ext}")))
    # Cap per-root file count (bounded scan).
    capped: list[Path] = []
    for path in files:
        if len(capped) >= 200:
            break
        try:
            path.relative_to(repo_root)
        except ValueError:
            continue
        capped.append(path)
    return capped


def _pyproject_dependencies(repo_root: Path) -> set[str]:
    deps: set[str] = set()
    pyproject = repo_root / "pyproject.toml"
    if not pyproject.is_file():
        return deps
    text = _read_text(pyproject)
    for match in re.finditer(r'^\s*([a-zA-Z0-9_.-]+)\s*=\s*["\']', text, re.M):
        deps.add(match.group(1).lower())
    return deps


def _discover_from_text_chunk(
    source: str,
    text: str,
    *,
    deps: set[str],
    same_file_httpx: bool,
) -> list[DiscoveredOperation]:
    found: list[DiscoveredOperation] = []
    has_httpx_surface = bool(_HTTPX_CLIENT.search(text)) or (
        same_file_httpx and "httpx" in text.lower()
    )
    has_max_integration = bool(_MAX_INTEGRATION_MARKDOWN.search(text)) or bool(
        _MAX_INTEGRATION_CODE.search(text)
    )

    if has_httpx_surface and has_max_integration:
        found.append(
            DiscoveredOperation(
                obligation_id="max-send",
                category="EXTERNAL_API",
                evidence_source=source,
                rationale="httpx client with explicit MAX API integration signals",
            )
        )

    if _HTTPX_CLIENT.search(text) and "httpx" in deps:
        found.append(
            DiscoveredOperation(
                obligation_id="httpx-client",
                category="LIBRARY",
                evidence_source=source,
                rationale="httpx client API referenced in scoped surface",
            )
        )

    if (
        re.search(r"\bpydantic\.|from\s+pydantic\s+import|import\s+pydantic\b", text)
        and "pydantic" in deps
    ):
        found.append(
            DiscoveredOperation(
                obligation_id="pydantic-models",
                category="LIBRARY",
                evidence_source=source,
                rationale="pydantic API referenced with declared dependency",
            )
        )
    return found


def _technology_assessment_required(change_root: Path) -> bool:
    design = change_root / "design.md"
    if not design.is_file():
        return False
    return bool(
        re.search(
            r"(?m)^TECHNOLOGY_ASSESSMENT\s*\nREQUIRED:\s*yes\b",
            _read_text(design),
            re.IGNORECASE,
        )
    )


def discover_for_change(change_root: Path, repo_root: Path) -> DiscoveryResult:
    """Discovery: Change artifacts + bounded code surface + lockfile deps."""

    result = DiscoveryResult()
    if not change_root.is_dir():
        result.blockers.append(f"change root missing: {change_root}")
        return result

    deps = _pyproject_dependencies(repo_root)
    for name in _PYPROJECT_CANDIDATES:
        path = repo_root / name
        if path.is_file():
            result.scanned_paths.append(name)

    merged: dict[str, DiscoveredOperation] = {}
    for source, text in _collect_change_text(change_root):
        result.scanned_paths.append(source)
        for item in _discover_from_text_chunk(
            source, text, deps=deps, same_file_httpx=False
        ):
            merged[item.obligation_id] = item

    affected_roots = _parse_affected_surface_paths(change_root, repo_root)
    code_files = _expand_code_files(affected_roots, repo_root)
    for code_path in code_files:
        rel = code_path.relative_to(repo_root).as_posix()
        result.scanned_paths.append(rel)
        text = _read_text(code_path)
        has_httpx = bool(_HTTPX_CLIENT.search(text))
        for item in _discover_from_text_chunk(
            rel,
            text,
            deps=deps,
            same_file_httpx=has_httpx,
        ):
            merged[item.obligation_id] = item

    ta_required = _technology_assessment_required(change_root)
    if ta_required and not affected_roots and not any(
        op.obligation_id == "max-send" for op in merged.values()
    ):
        result.blockers.append(
            "DISCOVERY_SURFACE_UNRESOLVED: TECHNOLOGY_ASSESSMENT REQUIRED:yes "
            "but ARCHITECTURE_AFFECTED_SURFACE lists no resolvable code paths"
        )

    result.operations = list(merged.values())
    if not result.operations and not result.blockers:
        result.limitations.append(
            "static discovery V1: no obligations matched in scoped artifacts/code; "
            "dynamic/runtime-only integrations may evade heuristics"
        )
    return result


def discovery_gaps(
    discovered: DiscoveryResult,
    design_obligation_ids: set[str],
) -> list[DiscoveredOperation]:
    """Операции, найденные discovery, но отсутствующие в design manifest."""

    missing: list[DiscoveredOperation] = []
    for item in discovered.operations:
        if item.obligation_id not in design_obligation_ids:
            missing.append(item)
    return missing
