"""Сборка capability manifest для apply-design hop."""

from __future__ import annotations

from opsx.project_context import resource_path

import hashlib
from pathlib import Path

from agents.capabilities.manifest import (
    AgentCapabilityManifest,
    DesignSubmission,
    ReadDomain,
    SourceDescriptor,
)
from agents.capabilities.path_policy import load_product_roots
from agents.implementation.apply_design.knowledge_index import prior_design_index
from workflows.artifact_paths import apply_design_artifact_md_path, apply_design_artifact_path
from workflows.implementation.receipt_loader import apply_design_file_sha256

INPUT_SCOPE_INCOMPLETE = "INPUT_SCOPE_INCOMPLETE"

_PLANNING_TASKS = "planning:tasks_md"
_PLANNING_DESIGN = "planning:design_md"
_PLANNING_PROPOSAL = "planning:proposal_md"
_PLANNING_OWNER = "planning:owner_contract_md"

PLANNING_SOURCE_ALIASES = frozenset(
    {
        _PLANNING_TASKS,
        _PLANNING_DESIGN,
        _PLANNING_PROPOSAL,
        _PLANNING_OWNER,
    }
)


def build_apply_design_manifest(
    change_id: str,
    *,
    repo_root: Path,
) -> AgentCapabilityManifest:
    """Manifest с общим read scope и единственной записью APPLY-DESIGN."""
    repo = repo_root.resolve()
    change_root = resource_path(repo, "openspec", "changes", change_id)
    product_roots = load_product_roots(repo)
    read_domains = {
        "product": ReadDomain("product", product_roots),
        "tests": ReadDomain("tests", ("tests",)),
        "migrations": ReadDomain("migrations", ("migrations",)),
        "docs": ReadDomain("docs", ("docs",)),
        "changes": ReadDomain("changes", ("openspec/changes",)),
        "specs": ReadDomain("specs", ("openspec/specs",)),
        "decisions": ReadDomain("decisions", ("openspec/decisions",)),
        "prior_designs": ReadDomain("prior_designs", ()),
    }
    prior = dict(prior_design_index(repo))
    readable: dict[str, SourceDescriptor] = {}
    gaps: list[str] = []
    _register_planning_file(
        readable,
        change_root,
        repo,
        _PLANNING_TASKS,
        change_root / "tasks.md",
        "changes",
        gaps,
        required=True,
        gap_code="tasks_md",
    )
    has_design_doc = _register_planning_file(
        readable,
        change_root,
        repo,
        _PLANNING_DESIGN,
        change_root / "design.md",
        "changes",
        gaps,
        required=False,
        gap_code="design_md",
    )
    has_proposal = _register_planning_file(
        readable,
        change_root,
        repo,
        _PLANNING_PROPOSAL,
        change_root / "proposal.md",
        "changes",
        gaps,
        required=False,
        gap_code="proposal_md",
    )
    has_spec = _register_specs(change_root, repo, readable)
    _register_planning_file(
        readable,
        change_root,
        repo,
        _PLANNING_OWNER,
        change_root / "owner-contract.md",
        "changes",
        gaps,
        required=False,
        gap_code="owner_contract_md",
    )
    if not has_design_doc and not has_proposal and not has_spec:
        gaps.append("planning_spec_or_design")
    planning_complete = "tasks_md" not in gaps and "planning_spec_or_design" not in gaps
    d_sha = apply_design_file_sha256(change_id, repo_root=repo) or ""
    json_path = apply_design_artifact_path(repo, change_id)
    md_path = apply_design_artifact_md_path(repo, change_id)
    submission: DesignSubmission | None = None
    if planning_complete:
        submission = DesignSubmission(
            change_id=change_id,
            json_path=json_path,
            md_path=md_path,
            base_digest=d_sha,
        )
    return AgentCapabilityManifest(
        change_id=change_id,
        repo_root=repo,
        read_domains=read_domains,
        readable_sources=readable,
        prior_designs=prior,
        design_submission=submission,
        planning_complete=planning_complete,
        planning_gaps=tuple(gaps),
    )


def _register_planning_file(
    readable: dict[str, SourceDescriptor],
    change_root: Path,
    repo: Path,
    source_id: str,
    path: Path,
    domain: str,
    gaps: list[str],
    *,
    required: bool,
    gap_code: str,
) -> bool:
    if path.is_file():
        readable[source_id] = SourceDescriptor(source_id=source_id, domain=domain)
        rel = path.resolve().relative_to(repo).as_posix()
        readable[f"changes:{rel}"] = SourceDescriptor(
            source_id=f"changes:{rel}",
            domain=domain,
        )
        return True
    if required:
        gaps.append(gap_code)
    return False


def _register_specs(
    change_root: Path,
    repo: Path,
    readable: dict[str, SourceDescriptor],
) -> bool:
    specs = change_root / "specs"
    if not specs.is_dir():
        return False
    found = False
    for path in sorted(specs.rglob("spec.md")):
        if not path.is_file():
            continue
        found = True
        rel = path.resolve().relative_to(repo).as_posix()
        sid = f"changes:{rel}"
        readable[sid] = SourceDescriptor(source_id=sid, domain="changes")
    return found


def planning_source_path(repo: Path, source_id: str, change_id: str) -> Path | None:
    """Внутренний путь planning id текущего change."""
    change_root = resource_path(repo, "openspec", "changes", change_id)
    mapping = {
        _PLANNING_TASKS: change_root / "tasks.md",
        _PLANNING_DESIGN: change_root / "design.md",
        _PLANNING_PROPOSAL: change_root / "proposal.md",
        _PLANNING_OWNER: change_root / "owner-contract.md",
    }
    path = mapping.get(source_id)
    if path is not None and path.is_file():
        return path
    return None


def file_digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
