"""Routing markers by content; per-check receipts independently decide reuse."""

from __future__ import annotations

from opsx.project_context import resource_path

import hashlib
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping

from agents.check_receipts import content_digest
from agents.state import GraphState

CYCLE_CHECKS_STATE_KEY = "cycle_checks"
"""Ключ в ``GraphState``: вложенная группа зелёных проверок круга."""


class CheckName(StrEnum):
    """Имена зелёных проверок verify-цикла (не id узлов LangGraph)."""

    FULL_VERIFY_CYCLE = "full_verify_cycle"
    IMPL_VERIFY = "impl_verify"
    SCENARIO_COVERAGE = "scenario_coverage"
    GATE_REPAIR = "gate_repair"
    PROJECT_BUG = "project_bug"
    BUGBOT = "bugbot"
    INFRA_TESTS = "infra_tests"
    SECURITY = "security"
    APPLY_DESIGN = "apply_design"
    APPLY_DESIGN_REVIEW = "apply_design_review"
    APPLY_SCAFFOLD = "apply_scaffold"
    APPLY_RED = "apply_red"
    APPLY_IMPLEMENT = "apply_implement"
    APPLY_GREEN = "apply_green"
    SCENARIO_COVERAGE_PLAN = "scenario_coverage_plan"
    SCENARIO_TEST_AUTHOR = "scenario_test_author"


# Routing-only source marker. Durable receipts use narrower, independently
# validated input and verifier identities for each check. This marker must
# NEVER authorize skipping an expensive check by itself.
_ROUTING_PRODUCT_DIRS = ("src", "tests", "migrations", "configs")
_ROUTING_PRODUCT_CONFIGS = (
    "pyproject.toml", "poetry.lock", "pytest.ini", "mypy.ini",
    "pyrightconfig_strict.json", "pyrightconfig.json", "setup.cfg",
)
_ROUTING_PLAN_FILES = ("proposal.md", "design.md", "tasks.md", "owner-contract.md")
_ROUTING_SUFFIXES = frozenset((".py", ".sql", ".toml", ".json", ".md", ".yaml", ".yml", ".ini", ".cfg"))

_CHECK_WORKFLOW_RECEIPTS: dict[CheckName, Any] = {}


def register_check_workflow_receipt(receipt: Any) -> None:
    """Зарегистрировать per-check routing receipt (вызов из ``build_verify_graph``)."""
    check_name = receipt.check_name
    _CHECK_WORKFLOW_RECEIPTS[check_name] = receipt


def registered_check_workflow_receipt(name: CheckName) -> Any | None:
    """Вернуть зарегистрированный ``CheckWorkflowReceipt`` для ``name`` или ``None``."""
    return _CHECK_WORKFLOW_RECEIPTS.get(name)


def resolve_code_fingerprint(state: Mapping[str, Any]) -> str | None:
    """Routing content marker: no Git HEAD, commit or branch identity.

    It cannot be used as evidence. Every expensive hop independently reads
    a product- and verifier-bound receipt before choosing REUSED_PASS.
    """
    raw = state.get("repository_path") or state.get("workspace")
    if not raw:
        return None
    root = Path(str(raw)).resolve()
    try:
        paths: set[Path] = set()
        for dirname in _ROUTING_PRODUCT_DIRS:
            folder = resource_path(root, dirname)
            if folder.is_dir():
                paths.update(
                    path for path in folder.rglob("*")
                    if path.is_file()
                    and path.suffix.lower() in _ROUTING_SUFFIXES
                    and path.relative_to(root).parts[:2] != ("tests", "opsx")
                )
        for filename in _ROUTING_PRODUCT_CONFIGS:
            path = resource_path(root, filename)
            if path.is_file():
                paths.add(path)
        change_id = str(state.get("change_id") or "").strip()
        if change_id and "/" not in change_id and "\\" not in change_id:
            change = resource_path(root, "openspec", "changes", change_id)
            for filename in _ROUTING_PLAN_FILES:
                path = change / filename
                if path.is_file():
                    paths.add(path)
            specs = change / "specs"
            if specs.is_dir():
                paths.update(specs.rglob("*.md"))
            decisions = change / "decisions"
            if decisions.is_dir() and not decisions.is_symlink():
                paths.update(decisions.rglob("*.md"))
        if not paths:
            return None
        product = content_digest(root, paths)
        plan = state.get("plan_artifact_source_digest")
        suffix = str(plan) if isinstance(plan, str) and plan else ""
        return hashlib.sha256(
            ("routing-checks-content-v1\\0" + product + "\\0" + suffix).encode()
        ).hexdigest()
    except (OSError, ValueError):
        return None


def _read_group(state: Mapping[str, Any]) -> dict[str, Any]:
    raw = state.get(CYCLE_CHECKS_STATE_KEY)
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def _read_established(group: Mapping[str, Any]) -> dict[str, str]:
    raw = group.get("established")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, str) and value.strip():
            out[key] = value.strip()
    return out


class CycleChecks:
    """Зелёные проверки verify-цикла на текущем отпечатке кода."""

    def __init__(self, state: Mapping[str, Any]) -> None:
        self._state = state
        self._group = _read_group(state)
        self._established = _read_established(self._group)
        self._fingerprint = resolve_code_fingerprint(state)

    def is_established(self, name: CheckName) -> bool:
        """True, если проверка ``name`` зелёная на текущем отпечатке кода."""
        receipt = registered_check_workflow_receipt(name)
        if receipt is not None:
            from check_workflow_receipt import CheckWorkflowReceiptStatus

            return receipt.check(self._state) is CheckWorkflowReceiptStatus.MATCH
        if not self._fingerprint:
            return False
        return self._established.get(name.value) == self._fingerprint

    def establish(self, name: CheckName) -> dict[str, Any]:
        """Зафиксировать проверку ``name`` зелёной на текущем отпечатке кода.

        Returns:
            Patch для LangGraph: ``{cycle_checks: {established: ...}}``.
        """
        fingerprint = self._fingerprint
        if not fingerprint:
            return {}
        established = dict(self._established)
        established[name.value] = fingerprint
        return {CYCLE_CHECKS_STATE_KEY: {"established": established}}

    def invalidate_except(self, keep: CheckName) -> dict[str, Any]:
        """Снять все ``cycle_checks.established`` (низкоуровневый сброс; предпочитайте ``establish_check_circle``)."""
        _ = keep
        return {CYCLE_CHECKS_STATE_KEY: {"established": {}}}

    def establish_check_circle(self, name: CheckName, *, circle_changed_code: bool) -> dict[str, Any]:
        """Зелёный check→apply круг: ``establish``; при смене кода в круге — только ``name`` на HEAD."""
        fingerprint = self._fingerprint
        if not fingerprint:
            return {}
        if circle_changed_code:
            established: dict[str, str] = {name.value: fingerprint}
        else:
            established = dict(self._established)
            established[name.value] = fingerprint
        return {CYCLE_CHECKS_STATE_KEY: {"established": established}}

    def invalidate(self) -> dict[str, Any]:
        """Снять зелёные проверки, если код изменился относительно зафиксированных.

        Returns:
            Пустой dict, если отпечаток тот же и проверки остаются;
            иначе patch с обновлённой (возможно пустой) группой ``established``.
        """
        if not self._established:
            return {}
        fingerprint = self._fingerprint
        if not fingerprint:
            return {CYCLE_CHECKS_STATE_KEY: {"established": {}}}
        still_valid = {
            key: stored
            for key, stored in self._established.items()
            if stored == fingerprint
        }
        if still_valid == self._established:
            return {}
        return {CYCLE_CHECKS_STATE_KEY: {"established": still_valid}}


def cycle_checks(state: GraphState) -> CycleChecks:
    """Зелёные проверки verify-цикла. Не checkpoint узла."""
    return CycleChecks(state)
