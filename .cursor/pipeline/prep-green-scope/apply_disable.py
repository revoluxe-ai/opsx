"""Materialize disable delta with preflight (prepare → apply)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from config_apply import apply_pyright_ignore
from disable_plan import DisablePlan
from pyproject_pytest import PYPROJECT_REL, validate_pyproject_toml
from pytest_disable_validate import (
    PytestDisablePlan,
    apply_validated_pytest_plan,
    collect_pytest_file_paths,
    validate_pytest_disable,
    verify_pytest_plan_applied,
)

_SKIP_REASON_PREFIX = "prep-green-scope:"
_MYPY_OVERRIDE = re.compile(
    r"\[\[tool\.mypy\.overrides\]\]\s*\nmodule\s*=\s*\"([^\"]+)\"\s*\nignore_errors\s*=\s*true",
    re.MULTILINE,
)
_DEF_LINE = re.compile(r"^(async\s+def|def)\s+(\w+)\s*\(")


@dataclass
class AppliedDelta:
    """Successfully applied mutations only."""

    pyright_files: list[str]
    mypy_modules: list[str]
    pytest_nodes: list[str]


@dataclass
class PreparedDelta:
    pyright_files: list[str]
    mypy_modules: list[str]
    pytest_plans: list[tuple[str, PytestDisablePlan]]


@dataclass
class PrepareResult:
    ok: bool
    prepared: PreparedDelta | None = None
    unsupported_pytest_nodes: list[str] = field(default_factory=list)
    recovery_contract: str = ""


@dataclass
class ApplyDeltaResult:
    applied: AppliedDelta
    not_applied: list[str] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    recovery_contract: str = "none"


def _existing_mypy_override_modules(text: str) -> set[str]:
    return set(_MYPY_OVERRIDE.findall(text))


def _validate_pyright_targets(root: Path, files: list[str]) -> list[str]:
    bad: list[str] = []
    for rel in files:
        path = root / rel.split(":")[0]
        if not path.is_file():
            bad.append(rel)
    return bad


def _validate_mypy_modules(modules: list[str]) -> list[str]:
    return [m for m in modules if not str(m).strip()]


def prepare_delta(root: Path, plan: DisablePlan) -> PrepareResult:
    """Validate all mutation shapes before any write."""

    bad_pyright = _validate_pyright_targets(root, plan.pyright_files)
    if bad_pyright:
        return PrepareResult(
            ok=False,
            unsupported_pytest_nodes=bad_pyright,
            recovery_contract="no_writes_attempted:invalid_pyright_target",
        )
    bad_mypy = _validate_mypy_modules(plan.mypy_modules)
    if bad_mypy:
        return PrepareResult(
            ok=False,
            unsupported_pytest_nodes=bad_mypy,
            recovery_contract="no_writes_attempted:invalid_mypy_module",
        )
    unsupported: list[str] = []
    pytest_plans: list[tuple[str, PytestDisablePlan]] = []
    for node in plan.pytest_nodes:
        validated = validate_pytest_disable(root, node)
        if validated is None:
            unsupported.append(node)
        else:
            pytest_plans.append((node, validated))
    if unsupported:
        return PrepareResult(
            ok=False,
            unsupported_pytest_nodes=unsupported,
            recovery_contract="no_writes_attempted",
        )
    return PrepareResult(
        ok=True,
        prepared=PreparedDelta(
            pyright_files=list(plan.pyright_files),
            mypy_modules=list(plan.mypy_modules),
            pytest_plans=pytest_plans,
        ),
    )


def apply_prepared_delta(root: Path, prepared: PreparedDelta, *, reason_suffix: str) -> ApplyDeltaResult:
    """Apply a fully preflighted delta (pytest first, then static configs)."""

    reason = f"{_SKIP_REASON_PREFIX} {reason_suffix}".strip()
    changed: list[str] = []
    applied_nodes: list[str] = []
    plans_only = [p for _, p in prepared.pytest_plans]

    for node_id, pplan in prepared.pytest_plans:
        if not apply_validated_pytest_plan(pplan, reason=reason):
            return ApplyDeltaResult(
                applied=AppliedDelta([], [], []),
                not_applied=[node_id],
                files_changed=changed,
                recovery_contract=f"pytest_apply_failed:{node_id}",
            )
        if not verify_pytest_plan_applied(pplan, reason=reason):
            return ApplyDeltaResult(
                applied=AppliedDelta([], [], []),
                not_applied=[node_id],
                files_changed=changed,
                recovery_contract=f"pytest_verify_failed:{node_id}",
            )
        applied_nodes.append(node_id)
        changed.append(pplan.path.relative_to(root).as_posix())

    _ = collect_pytest_file_paths(plans_only)

    if prepared.pyright_files:
        _ = apply_pyright_ignore(root, prepared.pyright_files)
        changed.append("pyrightconfig_strict.json")
    if prepared.mypy_modules:
        path = root / PYPROJECT_REL
        text = path.read_text(encoding="utf-8")
        known = _existing_mypy_override_modules(text)
        to_add = sorted({m for m in prepared.mypy_modules if m and m not in known})
        if to_add:
            blocks = [
                f'[[tool.mypy.overrides]]\nmodule = "{mod}"\nignore_errors = true\n'
                for mod in to_add
            ]
            path.write_text(text.rstrip() + "\n" + "".join(blocks), encoding="utf-8")
            validate_pyproject_toml(path)
            changed.append("pyproject.toml")
    return ApplyDeltaResult(
        applied=AppliedDelta(
            pyright_files=list(prepared.pyright_files),
            mypy_modules=list(prepared.mypy_modules),
            pytest_nodes=applied_nodes,
        ),
        files_changed=sorted(set(changed)),
        recovery_contract="none",
    )


def apply_disable_delta(root: Path, plan: DisablePlan, *, reason_suffix: str = "baseline red") -> ApplyDeltaResult:
    """Preflight + apply; zero writes when pytest shape unsupported."""

    prep = prepare_delta(root, plan)
    if not prep.ok or prep.prepared is None:
        return ApplyDeltaResult(
            applied=AppliedDelta([], [], []),
            not_applied=list(plan.pytest_nodes),
            recovery_contract=prep.recovery_contract or "no_writes_attempted",
        )
    return apply_prepared_delta(root, prep.prepared, reason_suffix=reason_suffix)


def apply_mypy_modules_monotonic(root: Path, modules: list[str]) -> list[str]:
    """Append-only mypy overrides (legacy acceptance helper)."""

    prep = PreparedDelta([], list(modules), [])
    result = apply_prepared_delta(root, prep, reason_suffix="monotonic")
    return result.applied.mypy_modules


def snapshot_mutation_state(root: Path) -> str:
    """Hash of pyright ignore + pyproject for partial-apply tests."""

    parts: list[str] = []
    pr = root / "pyrightconfig_strict.json"
    if pr.is_file():
        parts.append(pr.read_text(encoding="utf-8"))
    pj = root / PYPROJECT_REL
    if pj.is_file():
        parts.append(pj.read_text(encoding="utf-8"))
    return json.dumps(parts, sort_keys=True)
