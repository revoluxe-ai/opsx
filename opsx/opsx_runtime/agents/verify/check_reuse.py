"""Durable read-only verification reuse, independent of LangGraph.

Implementation review reuses a validated clean verdict on identical input
files. Other review roles keep their existing prompt/model/baseline policy.
"""
from __future__ import annotations

from opsx.project_context import resource_path

import hashlib
import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, TypeVar

from pydantic import BaseModel, ValidationError

from agents.check_receipts import best_effort_write_pass, content_digest, read_pass
from workflows.implementation.node_subprocess import run_subprocess_utf8

_LOG = logging.getLogger(__name__)
_REVIEW_INPUT_CONTRACT = "read_only_review_v2"
_IMPL_INPUT_CONTRACT = "implementation_review_v1"
_SUFFIXES = {".py", ".sql", ".toml", ".yaml", ".yml", ".json", ".ini", ".cfg", ".md"}
_ROLE_DIRECTORIES = {
    "impl_verify": "impl_verify",
    "archive_verify": "archive_verify",
    "project_bug": "project_bug_hunt",
    "bugbot": "bugbot",
    "security": "security_review",
    "coverage_activation": "coverage_activation",
}
_NEEDS_BASELINE = frozenset(("project_bug", "bugbot", "security"))
"""Роли с узким product scope: только ``src/``, entry points и артефакты change."""
_PRODUCT_CODE_REVIEW_ROLES = frozenset({"project_bug", "bugbot", "security"})
_REPO_ENTRY_POINT_FILES = ("run_opsx_main.py",)
_STATIC_CHANGE_PLAN_FILES = ("proposal.md", "design.md", "tasks.md")
# Explicit per-check invalidation policy. Editing any directory below changes the
# product identity for that check, except paths expressly excluded by policy.
_PRODUCT_DIRS_BY_ROLE: dict[str, tuple[str, ...]] = {
    "impl_verify": ("src", "migrations"),
    "archive_verify": ("src", "migrations"),
    "project_bug": ("src", "tests"),
    "bugbot": ("src", "tests"),
    "security": ("src", "tests"),
    "coverage_activation": ("src", "migrations", "tests"),
}
_PRODUCT_ROOT_FILES_BY_ROLE: dict[str, tuple[str, ...]] = {
    "impl_verify": ("pyproject.toml", "poetry.lock", "setup.cfg"),
    "archive_verify": ("pyproject.toml", "poetry.lock", "setup.cfg"),
    "project_bug": (),
    "bugbot": (),
    "security": (),
    "coverage_activation": ("pyproject.toml", "poetry.lock", "pytest.ini", "conftest.py", "mypy.ini", "pyrightconfig_strict.json", "setup.cfg"),
}
# Role policy directories are independent. No role's policy identity includes
# an unrelated role, global verification orchestration, or SDK transport.
_VERIFIER_POLICY_DIR_BY_ROLE: dict[str, tuple[str, ...]] = {
    "archive_verify": ("opsx/opsx_runtime/agents/verify/archive_verify",),
    "project_bug": ("opsx/opsx_runtime/agents/verify/project_bug_hunt",),
    "bugbot": ("opsx/opsx_runtime/agents/verify/bugbot",),
    "security": ("opsx/opsx_runtime/agents/verify/security_review",),
    "coverage_activation": ("opsx/opsx_runtime/agents/verify/coverage_activation",),
}
_VERIFIER_SHARED_FILES = (
    "opsx/opsx_runtime/agents/check_receipts.py",
    "opsx/opsx_runtime/agents/verify/check_reuse.py",
    "opsx/opsx_runtime/agents/response_schema.py",
    "opsx/opsx_runtime/agents/state.py",
)
T = TypeVar("T", bound=BaseModel)


def _json(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_json(value)).hexdigest()


def _files_under_product_dir(repo: Path, root_rel: str) -> set[Path]:
    """Файлы product-дерева с допустимыми суффиксами (без ``tests/opsx``)."""
    root = resource_path(repo, root_rel)
    if not root.is_dir():
        return set()
    return {
        p
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix.lower() in _SUFFIXES
        and not (root.name == "tests" and p.relative_to(root).parts[0] == "opsx")
    }


def _product_entry_point_files(repo: Path) -> set[Path]:
    """Явные entry points вне ``src/`` + зарегистрированные файлы под ``src/``."""
    from agents.verify.coverage_activation.mypy_static import load_registered_mypy_paths

    paths: set[Path] = set()
    for name in _REPO_ENTRY_POINT_FILES:
        candidate = resource_path(repo, name)
        if candidate.is_file():
            paths.add(candidate)
    for rel in load_registered_mypy_paths(repo):
        norm = rel.replace("\\", "/").lstrip("./")
        if not norm.startswith("src/"):
            continue
        candidate = resource_path(repo, norm)
        if candidate.is_file():
            paths.add(candidate)
    return paths


def _change_planning_files(repo: Path, change_id: str, *, role: str) -> set[Path]:
    """Статичные плановые артефакты change (без verify/logs/owner-contract)."""
    change = resource_path(repo, "openspec", "changes", change_id)
    paths: set[Path] = set()
    if role in _PRODUCT_CODE_REVIEW_ROLES:
        plan_names = _STATIC_CHANGE_PLAN_FILES
    else:
        plan_names = (*_STATIC_CHANGE_PLAN_FILES, "owner-contract.md")
    for name in plan_names:
        p = change / name
        if p.is_file():
            paths.add(p)
    specs = change / "specs"
    if specs.is_dir() and not specs.is_symlink():
        paths.update(
            p
            for p in specs.rglob("*.md")
            if p.is_file() and not p.is_symlink()
        )
    if role == "impl_verify":
        decisions = change / "decisions"
        if decisions.is_symlink():
            raise ValueError("REVIEW_DECISIONS_UNSAFE")
        if decisions.is_dir():
            for path in decisions.rglob("*"):
                if path.is_symlink():
                    raise ValueError("REVIEW_DECISIONS_UNSAFE")
                if path.is_file() and path.suffix.lower() == ".md":
                    paths.add(path)
    return paths


def _comparison_scope_paths(repo: Path, role: str) -> list[str]:
    """Пути для ``git diff`` baseline при review с diff-семантикой."""
    paths: list[str] = list(_PRODUCT_DIRS_BY_ROLE[role])
    if role in _PRODUCT_CODE_REVIEW_ROLES:
        paths.extend(_REPO_ENTRY_POINT_FILES)
        from agents.verify.coverage_activation.mypy_static import load_registered_mypy_paths

        for rel in load_registered_mypy_paths(repo):
            norm = rel.replace("\\", "/").lstrip("./")
            if norm.startswith("src/"):
                paths.append(norm)
    else:
        paths.extend(_PRODUCT_ROOT_FILES_BY_ROLE[role])
    return paths


def _source_files(repo: Path, change_id: str, role: str) -> set[Path]:
    """Conservative, bounded scope, without unrelated logs or receipts."""
    change = resource_path(repo, "openspec", "changes", change_id)
    if not change.is_dir() or change.is_symlink():
        raise ValueError("REVIEW_CHANGE_INVALID")
    paths: set[Path] = set()
    # PHASE_IMPL explicitly audits production code, not test coverage.
    # project_bug / bugbot / security: ``src/**``, product ``tests/**`` (не tests/opsx).
    for root_rel in _PRODUCT_DIRS_BY_ROLE[role]:
        paths.update(_files_under_product_dir(repo, root_rel))
    if role in _PRODUCT_CODE_REVIEW_ROLES:
        paths.update(_product_entry_point_files(repo))
    paths.update(_change_planning_files(repo, change_id, role=role))
    for name in _PRODUCT_ROOT_FILES_BY_ROLE[role]:
        p = resource_path(repo, name)
        if p.is_file():
            paths.add(p)
    if not paths:
        raise ValueError("REVIEW_INPUTS_EMPTY")
    return paths


def _verifier_contract_files(repo: Path, role: str) -> set[Path]:
    """OPSX code influences verifier authority, never product input identity."""
    paths = {
        resource_path(repo, name) for name in _VERIFIER_SHARED_FILES if (resource_path(repo, name)).is_file()
    }
    for root_rel in _VERIFIER_POLICY_DIR_BY_ROLE[role]:
        root = resource_path(repo, root_rel)
        if not root.is_dir() or root.is_symlink():
            raise ValueError("REVIEW_VERIFIER_CONTRACT_UNBOUND")
        role_files = {
            p for p in root.rglob("*.py")
            if p.is_file() and "tests" not in p.relative_to(root).parts
        }
        if not role_files or any(p.is_symlink() for p in role_files):
            raise ValueError("REVIEW_VERIFIER_CONTRACT_UNBOUND")
        paths.update(role_files)
    if not paths:
        raise ValueError("REVIEW_VERIFIER_CONTRACT_UNBOUND")
    return paths


def _comparison_baseline_digest(repo: Path, role: str) -> str:
    """Hash relevant *working-tree vs baseline* differences, never Git HEAD.

    Changing branch/commit metadata alone cannot invalidate this reviewer.
    The baseline still matters because these roles explicitly review a diff.
    """
    root = run_subprocess_utf8(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=repo,
        timeout=8,
    )
    if root.returncode != 0 or not root.stdout.strip():
        return hashlib.sha256(b"opsx-comparison-baseline-unbound-v1").hexdigest()
    if Path(root.stdout.strip()).resolve() != repo.resolve():
        raise ValueError("REVIEW_GIT_ROOT_MISMATCH")
    for baseline in ("stage", "origin/stage"):
        probe = run_subprocess_utf8(
            ["git", "rev-parse", "--verify", "--quiet", baseline + "^{commit}"],
            cwd=repo,
            timeout=8,
        )
        if probe.returncode != 0 or not probe.stdout.strip():
            continue
        included = _comparison_scope_paths(repo, role)
        diff = run_subprocess_utf8(
            [
                "git", "diff", "--no-ext-diff", "--no-color", "--binary",
                baseline, "--", *included, ":(exclude)tests/opsx",
            ],
            cwd=repo,
            timeout=20,
        )
        if diff.returncode == 0:
            return hashlib.sha256(
                b"opsx-comparison-baseline-v2\\0" + diff.stdout.encode("utf-8")
            ).hexdigest()
    # Локальный прогон без origin/stage: reuse только по байтам product tree.
    return hashlib.sha256(b"opsx-comparison-baseline-unbound-v1").hexdigest()


@dataclass(frozen=True)
class ReviewReceiptKey:
    repo: Path
    change_id: str
    check: str
    digest: str  # product input digest only, independent of opsx/**
    execution_id: str
    verifier_contract_digest: str | None = None


def _receipt_check_name(role: str, level: int | None) -> str:
    """Имя durable receipt; bugbot/security/project_bug — один ключ на роль."""
    if role == "impl_verify":
        return "impl_semantic_review"
    if role in _PRODUCT_CODE_REVIEW_ROLES:
        return role
    if level is None:
        return role
    return f"{role}_level_{level}"


def _receipt_contract_level(role: str, level: int | None) -> int | None:
    """Level в verifier contract; для единого verify-круга не используется."""
    if role in _PRODUCT_CODE_REVIEW_ROLES:
        return None
    return level


def review_receipt_key(
    state: Mapping[str, Any], *, role: str, prompt: str,
    model: Mapping[str, Any] | None = None,
    level: int | None = None,
) -> ReviewReceiptKey | None:
    """Implementation review depends only on its product input files.

    Other review roles retain their existing policy until separately changed.
    """
    try:
        if role not in _ROLE_DIRECTORIES:
            raise ValueError("REVIEW_ROLE_UNKNOWN")
        repo = Path(str(state["repository_path"])).resolve()
        change_id = str(state["change_id"])
        execution_id = str(state.get("run_uuid") or "").strip()
        if not execution_id:
            return None
        if role == "impl_verify":
            # No reviewer code, prompt, model or workspace string enters the
            # implementation review identity. Keep the existing product hash
            # so previously completed IMPL_OK receipts remain reusable.
            return ReviewReceiptKey(
                repo, change_id, "impl_semantic_review",
                content_digest(repo, _source_files(repo, change_id, role)),
                execution_id,
            )
        config = model if model is not None else state.get("model")
        if not isinstance(config, Mapping) or not config:
            return None
        if not isinstance(config.get("id"), str) or not config["id"].strip():
            return None
        if level is not None and level not in (1, 2, 3):
            return None
        product_digest = content_digest(repo, _source_files(repo, change_id, role))
        if role in _NEEDS_BASELINE:
            # Review of changes has two product inputs: current source bytes
            # and the relevant baseline-vs-worktree content delta. Neither is
            # Git HEAD or verifier policy.
            product_digest = _digest({
                "product_source": product_digest,
                "product_comparison_delta": _comparison_baseline_digest(repo, role),
            })
        verifier_policy_digest = content_digest(repo, _verifier_contract_files(repo, role))
        contract_level = _receipt_contract_level(role, level)
        # Rendered prompt (diff pack, timestamps) must not invalidate durable PASS;
        # policy code + model tier already bound in verifier_policy_digest / model.
        _ = prompt
        verifier_contract_digest = _digest({
            "contract": _REVIEW_INPUT_CONTRACT,
            "role": role,
            "level": contract_level,
            "verifier_policy_digest": verifier_policy_digest,
            "model": dict(config),
        })
        check = _receipt_check_name(role, level)
        return ReviewReceiptKey(
            repo, change_id, check, product_digest, execution_id,
            verifier_contract_digest,
        )
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        _LOG.info("review_receipt_miss role=%s reason=%s", role, type(exc).__name__)
        return None


def _check_for_digest(check: str) -> str:
    if check == "impl_semantic_review":
        return "impl_verify"
    if check.startswith("scenario_supported_"):
        return "scenario_coverage"
    return check.replace("_level_1", "").replace("_level_2", "").replace("_level_3", "")


def read_clean_review(
    key: ReviewReceiptKey | None, *, model: type[T],
    pass_verdict: str, clean: Callable[[T], bool],
    role: str | None = None,
) -> tuple[T, dict[str, Any]] | None:
    if key is None:
        return None
    implementation = key.check == "impl_semantic_review"
    digest_role = role or _check_for_digest(key.check)
    try:
        input_paths = _source_files(key.repo, key.change_id, digest_role)
    except (ValueError, KeyError):
        input_paths = None
    item = read_pass(
        key.repo, change_id=key.change_id, check=key.check,
        contract=_IMPL_INPUT_CONTRACT if implementation else _REVIEW_INPUT_CONTRACT,
        input_digest=key.digest, pass_verdict=pass_verdict,
        verifier_contract_digest=key.verifier_contract_digest,
        input_paths=input_paths,
    )
    if item is None and implementation:
        # Legacy receipts already contain an independent product input hash.
        # Ignore their historical reviewer fingerprint; retain checksum,
        # exact product bytes, schema, verdict and original run provenance.
        item = read_pass(
            key.repo, change_id=key.change_id, check=key.check,
            contract=_REVIEW_INPUT_CONTRACT,
            input_digest=key.digest, pass_verdict=pass_verdict,
            ignore_verifier_contract=True,
            input_paths=input_paths,
        )
    if item is None:
        _LOG.info(
            "review_receipt miss check=%s change_id=%s reason=no_stored_pass",
            key.check,
            key.change_id,
        )
        return None
    try:
        answer = model.model_validate(item["result"])
        if not clean(answer):
            _LOG.info(
                "review_receipt miss check=%s change_id=%s reason=stored_not_clean",
                key.check,
                key.change_id,
            )
            return None
        _LOG.info(
            "review_receipt hit check=%s change_id=%s origin=%s",
            key.check,
            key.change_id,
            item.get("origin_execution_id"),
        )
        provenance = {
            "source": "durable_pass",
            "origin_execution_id": item["origin_execution_id"],
            "evidence_id": item["evidence_id"],
            "current_execution_id": key.execution_id,
            "input_digest": key.digest,
        }
        if not implementation:
            provenance["verifier_contract_digest"] = key.verifier_contract_digest
        return answer, provenance
    except (ValueError, TypeError, ValidationError):
        return None


def save_clean_review(
    key: ReviewReceiptKey | None, *, answer: BaseModel,
    pass_verdict: str, clean: bool, outcome: Any,
) -> bool:
    if key is None or not clean:
        return False
    if str(getattr(outcome, "status", "")).lower() not in ("finished", "completed"):
        return False
    run_id = str(getattr(outcome, "run_id", "") or "").strip()
    agent_id = str(getattr(outcome, "agent_id", "") or "").strip()
    if not run_id or not agent_id:
        return False
    return best_effort_write_pass(
        root=key.repo, change_id=key.change_id, check=key.check,
        contract=(
            _IMPL_INPUT_CONTRACT if key.check == "impl_semantic_review"
            else _REVIEW_INPUT_CONTRACT
        ), input_digest=key.digest,
        verifier_contract_digest=key.verifier_contract_digest,
        origin_execution_id=key.execution_id,
        evidence_id=f"{run_id}:{agent_id}",
        pass_verdict=pass_verdict, result=answer.model_dump(mode="json"),
    )


class ReadOnlyReviewDurablePass:
    """Чтение/запись durable PASS для security, bugbot, project_bug и аналогов."""

    @staticmethod
    def receipt_key(
        state: Mapping[str, Any],
        *,
        role: str,
        prompt: str,
        level: int | None = None,
    ) -> ReviewReceiptKey | None:
        return review_receipt_key(state, role=role, prompt=prompt, level=level)

    @staticmethod
    def read_cached(
        state: Mapping[str, Any],
        *,
        role: str,
        prompt: str,
        model: type[T],
        pass_verdict: str,
        clean: Callable[[T], bool],
        level: int | None = None,
    ) -> tuple[ReviewReceiptKey | None, tuple[T, dict[str, Any]] | None]:
        key = review_receipt_key(state, role=role, prompt=prompt, level=level)
        if key is None:
            _LOG.info("review_receipt miss role=%s reason=no_key", role)
            return None, None
        cached = read_clean_review(
            key,
            model=model,
            pass_verdict=pass_verdict,
            clean=clean,
            role=role,
        )
        return key, cached

    @staticmethod
    def save_clean(
        key: ReviewReceiptKey | None,
        *,
        answer: BaseModel,
        pass_verdict: str,
        clean: bool,
        outcome: Any,
    ) -> bool:
        return save_clean_review(
            key,
            answer=answer,
            pass_verdict=pass_verdict,
            clean=clean,
            outcome=outcome,
        )


def reused_review_transport_patch() -> dict[str, Any]:
    """Never reuse an old SDK session, run ID, usage or billable result."""
    return {
        "agent_id": "",
        "run_id": None,
        "execution_status": "REUSED_PASS",
        "usage": None,
        "cost": None,
        "cost_status": "not_applicable",
        "billing_error": None,
        "usage_source": "durable_pass",
        "validation_attempts": 0,
    }

