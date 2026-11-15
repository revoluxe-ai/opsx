"""Проверка путей внутри read domains без передачи path модели."""

from __future__ import annotations

from opsx.project_context import resource_path

import os
from pathlib import Path

_DENY_PREFIXES = (
    ".git/",
    ".env",
    "opsx/logs/",
    "opsx/runs/",
    "opsx/receipts/",
    ".cursor/",
    "node_modules/",
)

_DOMAIN_ROOTS: dict[str, tuple[str, ...]] = {
    "tests": ("tests",),
    "migrations": ("migrations",),
    "docs": ("docs",),
    "changes": ("openspec/changes",),
    "specs": ("openspec/specs",),
    "decisions": ("openspec/decisions",),
}


def load_product_roots(repo_root: Path) -> tuple[str, ...]:
    """``product.source_roots`` из ``opsx/project/capabilities.yaml``."""
    import yaml

    path = resource_path(repo_root, "opsx", "project", "capabilities.yaml")
    if not path.is_file():
        return ("src/stroy",)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return ("src/stroy",)
    product = raw.get("product")
    if not isinstance(product, dict):
        return ("src/stroy",)
    roots = product.get("source_roots")
    if not isinstance(roots, list) or not roots:
        return ("src/stroy",)
    return tuple(str(r).replace("\\", "/").strip("/") for r in roots if str(r).strip())


def domain_roots(domain: str, repo_root: Path) -> tuple[Path, ...]:
    """Абсолютные корни домена."""
    repo = repo_root.resolve()
    if domain == "product":
        return tuple(resource_path(repo, r) for r in load_product_roots(repo))
    if domain == "prior_designs":
        return ()
    rels = _DOMAIN_ROOTS.get(domain, ())
    return tuple(resource_path(repo, r) for r in rels)


def rel_posix(path: Path, repo_root: Path) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()


def path_denied(rel: str) -> bool:
    """True если префикс в deny list."""
    norm = rel.replace("\\", "/").lstrip("/")
    if not norm or norm in {".", "./"}:
        return True
    if ".." in Path(norm).parts:
        return True
    for part in norm.split("/"):
        if part == ".env" or part.endswith(".env"):
            return True
    for prefix in _DENY_PREFIXES:
        if norm == prefix.rstrip("/") or norm.startswith(prefix):
            return True
    return False


def path_in_domain(rel: str, domain: str, repo_root: Path) -> bool:
    """Файл под одним из корней домена и не в deny."""
    if path_denied(rel):
        return False
    norm = rel.replace("\\", "/").lstrip("/")
    roots = domain_roots(domain, repo_root)
    for base in roots:
        try:
            base_rel = rel_posix(base, repo_root)
        except ValueError:
            continue
        if norm == base_rel or norm.startswith(base_rel + "/"):
            return True
    return False


def resolve_domain_path(
    source_id: str,
    repo_root: Path,
) -> tuple[str, Path] | None:
    """``domain:rel`` → абсолютный путь, если разрешён."""
    if ":" not in source_id:
        return None
    domain, rel = source_id.split(":", 1)
    domain = domain.strip()
    rel = rel.strip().replace("\\", "/").lstrip("/")
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
        return None
    if path_denied(rel) or not path_in_domain(rel, domain, repo_root):
        return None
    target = (resource_path(repo_root, rel)).resolve()
    repo = repo_root.resolve()
    if repo not in target.parents and target != repo:
        return None
    if target == repo:
        return None
    try:
        rel_after = rel_posix(target, repo)
    except ValueError:
        return None
    if path_denied(rel_after) or not path_in_domain(rel_after, domain, repo_root):
        return None
    return domain, target
