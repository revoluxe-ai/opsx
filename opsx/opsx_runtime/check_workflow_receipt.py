"""Routing receipt и единая сверка recipe pins (файлы, scope, idempotency агентов)."""

from __future__ import annotations

from opsx.project_context import resource_path

import hashlib
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Mapping

from agents.check_receipts import content_digest
from agents.utils.receipt_file_identity import content_digest_matches_stored, file_pin_matches
from agents.state import GraphState
from workflows.verify.cycle_checks import (
    CYCLE_CHECKS_STATE_KEY,
    CheckName,
    _read_established,
    _read_group,
)

_FILE_SUFFIXES = frozenset(
    (".py", ".sql", ".toml", ".json", ".md", ".yaml", ".yml", ".ini", ".cfg", ".mjs", ".cjs")
)
_CHANGE_PLAN_FILES = ("proposal.md", "design.md", "tasks.md", "owner-contract.md")
_DIGEST_PREFIX = "check-workflow-receipt-v1\x00"


class CheckWorkflowReceiptStatus(StrEnum):
    """Результат проверки routing receipt относительно текущего scope."""

    MATCH = "match"
    NOT_ESTABLISHED = "not_established"
    STALE = "stale"
    DIGEST_UNAVAILABLE = "digest_unavailable"


def _repository_root(state: Mapping[str, Any]) -> Path | None:
    raw = state.get("repository_path") or state.get("workspace")
    if not raw:
        return None
    return Path(str(raw)).resolve()


class CheckWorkflowReceipt:
    """Зашитые файлы и слот рецепта: establish записывает, check сверяет с учётом CRLF/пробелов."""

    def __init__(
        self,
        check_name: CheckName | None = None,
        relative_paths: tuple[str, ...] = (),
        *,
        include_change_artifacts: bool = True,
        exclude_tests_opsx: bool = True,
        pinned: tuple[tuple[Path, str], ...] = (),
    ) -> None:
        self.check_name = check_name
        self._relative_paths = relative_paths
        self._include_change_artifacts = include_change_artifacts
        self._exclude_tests_opsx = exclude_tests_opsx
        self._pinned = tuple(pinned)

    @classmethod
    def none(cls) -> CheckWorkflowReceipt:
        """Агент без файлового входа: hop не пропускается, establish no-op."""
        return cls(check_name=None, relative_paths=())

    @staticmethod
    def matches_bytes(data: bytes, pinned_hex: str) -> bool:
        """Сверка одного pin с bytes файла (LF/CRLF/канонический текст)."""
        return file_pin_matches(data, pinned_hex)

    @staticmethod
    def matches_file(path: Path, pinned_hex: str) -> bool:
        """Сверка pin с файлом на диске."""
        if not path.is_file() or path.is_symlink():
            return False
        return file_pin_matches(path.read_bytes(), pinned_hex)

    def pinned_inputs_current(self) -> bool:
        """Все пары (path, content_hash) из конструктора сходятся с диском."""
        if not self._pinned:
            return False
        for path, pin in self._pinned:
            if not self.matches_file(path, pin):
                return False
        return True

    def agent_skip_hop(self, _state: Mapping[str, Any]) -> bool:
        """True только для ``pinned``: входы не изменились, hop можно не вызывать."""
        if self._pinned:
            return self.pinned_inputs_current()
        return False

    def check(self, state: Mapping[str, Any]) -> CheckWorkflowReceiptStatus:
        """Сравнить сохранённый routing receipt с текущим digest scope."""
        digest = self._digest(state)
        if not digest:
            return CheckWorkflowReceiptStatus.DIGEST_UNAVAILABLE
        group = _read_group(state)
        established = _read_established(group)
        stored = established.get(self.check_name.value)
        if not stored:
            return CheckWorkflowReceiptStatus.NOT_ESTABLISHED
        if stored == digest:
            return CheckWorkflowReceiptStatus.MATCH
        root = _repository_root(state)
        if root is not None:
            try:
                paths = self._collect_paths(root, state)
                if paths and self.digest_matches(stored, root, paths):
                    return CheckWorkflowReceiptStatus.MATCH
            except (OSError, ValueError):
                pass
        return CheckWorkflowReceiptStatus.STALE

    @staticmethod
    def digest_matches(stored_hex: str, root: Path, paths: Iterable[Path]) -> bool:
        """True, если сохранённый digest совпадает с scope на любой согласованной форме файлов."""
        base = root.resolve()
        rel_data: list[tuple[str, bytes]] = []
        for path in sorted(set(paths), key=lambda p: str(p)):
            full = path.resolve()
            if not full.is_file() or full.is_symlink():
                continue
            if not full.is_relative_to(base):
                continue
            rel = full.relative_to(base).as_posix()
            rel_data.append((rel, full.read_bytes()))
        if not rel_data:
            return False
        return content_digest_matches_stored(stored_hex, rel_data)

    def establish_patch(self, state: Mapping[str, Any]) -> dict[str, Any]:
        """Записать routing receipt на текущий digest (семантика ``establish_check_circle``)."""
        digest = self._digest(state)
        if not digest:
            return {}
        group = _read_group(state)
        established = dict(_read_established(group))
        circle_changed = state.get("verify_check_circle_code_changed") is True
        if circle_changed:
            established = {self.check_name.value: digest}
        else:
            established[self.check_name.value] = digest
        return {
            "verify_check_circle_code_changed": False,
            CYCLE_CHECKS_STATE_KEY: {"established": established},
        }

    def _digest(self, state: Mapping[str, Any]) -> str | None:
        root = _repository_root(state)
        if root is None:
            return None
        try:
            paths = self._collect_paths(root, state)
            if not paths:
                return None
            product = content_digest(root, paths)
            return hashlib.sha256((_DIGEST_PREFIX + product).encode()).hexdigest()
        except (OSError, ValueError):
            return None

    def _collect_paths(self, root: Path, state: Mapping[str, Any]) -> set[Path]:
        paths: set[Path] = set()
        for rel in self._relative_paths:
            rel_norm = rel.replace("\\", "/").strip("/")
            if not rel_norm:
                continue
            target = (resource_path(root, rel_norm)).resolve()
            try:
                target.relative_to(root)
            except ValueError:
                continue
            if target.is_file():
                if target.suffix.lower() in _FILE_SUFFIXES:
                    paths.add(target)
                continue
            if not target.is_dir():
                continue
            for path in target.rglob("*"):
                if not path.is_file():
                    continue
                if path.suffix.lower() not in _FILE_SUFFIXES:
                    continue
                if self._exclude_tests_opsx and self._is_tests_opsx(root, path):
                    continue
                paths.add(path)
        if self._include_change_artifacts:
            paths.update(self._change_artifact_paths(root, state))
        return paths

    @staticmethod
    def _is_tests_opsx(root: Path, path: Path) -> bool:
        try:
            rel = path.relative_to(root)
        except ValueError:
            return False
        return len(rel.parts) >= 2 and rel.parts[0] == "tests" and rel.parts[1] == "opsx"

    @staticmethod
    def _change_artifact_paths(root: Path, state: Mapping[str, Any]) -> set[Path]:
        change_id = str(state.get("change_id") or "").strip()
        if not change_id or "/" in change_id or "\\" in change_id:
            return set()
        change = resource_path(root, "openspec", "changes", change_id)
        if not change.is_dir() or change.is_symlink():
            return set()
        paths: set[Path] = set()
        for filename in _CHANGE_PLAN_FILES:
            path = change / filename
            if path.is_file():
                paths.add(path)
        specs = change / "specs"
        if specs.is_dir():
            paths.update(p for p in specs.rglob("*.md") if p.is_file())
        decisions = change / "decisions"
        if decisions.is_dir() and not decisions.is_symlink():
            paths.update(p for p in decisions.rglob("*.md") if p.is_file() and not p.is_symlink())
        return paths


_CHECK_WORKFLOW_PRODUCT_PATHS: tuple[str, ...] = (
    "src",
    "migrations",
    "tests",
    "configs",
    "pyproject.toml",
    "poetry.lock",
    "pytest.ini",
    "conftest.py",
    "mypy.ini",
    "pyrightconfig_strict.json",
    "pyrightconfig.json",
    "setup.cfg",
)


def _check_workflow_receipt(check_name: CheckName) -> CheckWorkflowReceipt:
    """Общий product-scope routing receipt для check-workflow subgraph."""
    return CheckWorkflowReceipt(
        check_name,
        _CHECK_WORKFLOW_PRODUCT_PATHS,
        include_change_artifacts=True,
        exclude_tests_opsx=True,
    )


def project_bug_workflow_receipt() -> CheckWorkflowReceipt:
    """Routing receipt project bug hunt (product scope по RECEIPT doc / check_reuse)."""
    return _check_workflow_receipt(CheckName.PROJECT_BUG)


def bugbot_workflow_receipt() -> CheckWorkflowReceipt:
    """Routing receipt bugbot (тот же product scope, что project_bug; verifier — bugbot)."""
    return _check_workflow_receipt(CheckName.BUGBOT)


def security_workflow_receipt() -> CheckWorkflowReceipt:
    """Routing receipt security (тот же product scope, что project_bug; verifier — security)."""
    return _check_workflow_receipt(CheckName.SECURITY)


_INFRA_EXTRA_PATHS = (
    "opsx/project/infra",
    "compose.test.yaml",
    "tests/docker-compose.test.yml",
)


def infra_tests_workflow_receipt() -> CheckWorkflowReceipt:
    """Routing receipt infra_tests: product scope + opsx/project/infra + compose."""
    base = _check_workflow_receipt(CheckName.INFRA_TESTS)
    extra_paths = tuple(dict.fromkeys((*base._relative_paths, *_INFRA_EXTRA_PATHS)))
    return CheckWorkflowReceipt(
        check_name=CheckName.INFRA_TESTS,
        relative_paths=extra_paths,
        include_change_artifacts=base._include_change_artifacts,
        exclude_tests_opsx=base._exclude_tests_opsx,
        pinned=base._pinned,
    )
