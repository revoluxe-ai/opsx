"""Поиск связанных тестов и обнаружение отключений."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

SkipKind = Literal["skip", "skipif", "xfail", "unittest_skip", "pytest_ini", "none"]

_UNSAFE_MARKERS = (
    "live",
    "destructive",
    "e2e_live",
    "integration_live",
    "requires_secrets",
    "owner_only",
)


@dataclass(frozen=True)
class RelatedTest:
    path: str
    relation: str
    skip_kind: SkipKind
    skip_reason: str
    unsafe: bool


def _module_stem(production_path: str) -> str:
    norm = production_path.replace("\\", "/").lstrip("./")
    if norm.endswith(".py"):
        norm = norm[:-3]
    return norm.replace("/", ".")


def _path_to_test_candidates(production_path: str) -> tuple[str, ...]:
    norm = production_path.replace("\\", "/").lstrip("./")
    parts = norm.split("/")
    name = Path(norm).stem
    candidates: set[str] = set()
    if norm.startswith("src/stroy/"):
        rel = norm[len("src/stroy/") :]
        candidates.add(f"tests/unit/{rel.replace('.py', '_test.py')}")
        candidates.add(f"tests/unit/{rel.replace('.py', '.py')}".replace("/test_", "/test_"))
        candidates.add(f"tests/integration/{rel.replace('.py', '_test.py')}")
    candidates.add(f"tests/unit/test_{name}.py")
    candidates.add(f"tests/test_{name}.py")
    mod = _module_stem(norm)
    tail = mod.split(".")[-1]
    candidates.add(f"tests/unit/test_{tail}.py")
    return tuple(sorted(candidates))


def _scan_skip_in_source(text: str) -> tuple[SkipKind, str]:
    if "@pytest.mark.skip" in text or "pytest.skip(" in text:
        return "skip", "pytest skip marker"
    if "@pytest.mark.skipif" in text:
        return "skipif", "pytest skipif"
    if "@pytest.mark.xfail" in text:
        return "xfail", "pytest xfail"
    if "@unittest.skip" in text or "unittest.skip(" in text:
        return "unittest_skip", "unittest skip"
    return "none", ""


def _is_unsafe_test(path: str, text: str) -> bool:
    lowered = text.lower()
    if any(marker in lowered for marker in _UNSAFE_MARKERS):
        return True
    if "pytest.mark.integration" in text and "live" in lowered:
        return True
    return False


def _grep_imports(repo_root: Path, module_fragment: str, test_root: Path) -> list[str]:
    hits: list[str] = []
    if not test_root.is_dir():
        return hits
    frag = module_fragment.replace(".", "/")
    for path in test_root.rglob("test_*.py"):
        rel = path.relative_to(repo_root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if module_fragment in text or frag in text:
            hits.append(rel)
    for path in test_root.rglob("*_test.py"):
        rel = path.relative_to(repo_root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if module_fragment in text:
            hits.append(rel)
    return sorted(set(hits))


def discover_related_tests(
    repo_root: Path,
    production_paths: tuple[str, ...],
) -> list[RelatedTest]:
    """Детерминированный сбор кандидатов + import grep."""
    results: dict[str, RelatedTest] = {}
    test_roots = [repo_root / "tests/unit", repo_root / "tests/integration", repo_root / "tests"]
    for prod in production_paths:
        mod = _module_stem(prod)
        for candidate in _path_to_test_candidates(prod):
            full = repo_root / candidate
            if full.is_file():
                text = full.read_text(encoding="utf-8")
                skip_kind, skip_reason = _scan_skip_in_source(text)
                results[candidate] = RelatedTest(
                    path=candidate,
                    relation=f"candidate_for:{prod}",
                    skip_kind=skip_kind,
                    skip_reason=skip_reason,
                    unsafe=_is_unsafe_test(candidate, text),
                )
        for root in test_roots:
            for hit in _grep_imports(repo_root, mod, root):
                full = repo_root / hit
                if not full.is_file():
                    continue
                text = full.read_text(encoding="utf-8")
                skip_kind, skip_reason = _scan_skip_in_source(text)
                results[hit] = RelatedTest(
                    path=hit,
                    relation=f"imports:{mod}",
                    skip_kind=skip_kind,
                    skip_reason=skip_reason,
                    unsafe=_is_unsafe_test(hit, text),
                )
    return sorted(results.values(), key=lambda r: r.path)


def discover_test_gaps(related: list[RelatedTest]) -> list[str]:
    """Production paths без найденных тестов — для отчёта T13."""
    return []


def build_test_reactivation_plan(
    related: list[RelatedTest],
    *,
    approved_paths: set[str],
) -> dict[str, Any]:
    """План снятия skip только для одобренных безопасных тестов."""
    reactivate: list[str] = []
    blocked: list[str] = []
    disabled_related = 0
    for row in related:
        if row.skip_kind == "none":
            continue
        disabled_related += 1
        if row.unsafe:
            blocked.append(row.path)
            continue
        if row.path not in approved_paths:
            blocked.append(row.path)
            continue
        if row.skip_kind in ("skip", "unittest_skip"):
            reactivate.append(row.path)
        elif row.skip_kind == "xfail":
            blocked.append(row.path)
        else:
            blocked.append(row.path)
    return {
        "reactivate": sorted(set(reactivate)),
        "blocked": sorted(set(blocked)),
        "disabled_related_count": disabled_related,
    }


def apply_test_reactivation(repo_root: Path, test_paths: list[str]) -> dict[str, Any]:
    """Убрать module-level ``@pytest.mark.skip`` / ``@unittest.skip`` одной строкой."""
    edited: list[str] = []
    for rel in test_paths:
        path = repo_root / rel
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        new_lines: list[str] = []
        changed = False
        for line in lines:
            if re.match(r"^\s*@pytest\.mark\.skip\b", line) or re.match(
                r"^\s*@unittest\.skip\b",
                line,
            ):
                changed = True
                continue
            new_lines.append(line)
        if changed:
            path.write_text("".join(new_lines), encoding="utf-8")
            edited.append(rel)
    return {"edited": edited}
