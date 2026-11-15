"""Operative gate для Bug Team RED_TEST_WORKER: live pytest + identity тестового файла.

Модель не вычисляет SHA; runtime подтверждает RED после parse JSON-ответа.
"""

from __future__ import annotations

from opsx.project_context import resource_path

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agents.verify.repair.run_pytest_tool import (
    run_repair_hop_pytest_sync,
    validate_repair_hop_pytest_node_id,
)
from workflows.repair.schemas import GreenEvidence, RedEvidence
from workflows.repo_paths import resolve_opsx_repository_root

_COLLECTION_MARKERS = re.compile(
    r"(ERROR collecting|ImportError while loading conftest|ModuleNotFoundError)",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class RedOperativeValidation:
    """Результат operative gate для hop RED_TEST_WORKER."""

    ok: bool
    test_content_identity: str | None
    errors: list[str]
    pytest_result: dict[str, Any] | None = None


def _resolve_repair_repo_root(repo_root: Path | str) -> Path:
    """Корень для pytest subprocess; в тестах допускается временный каталог."""
    candidate = Path(repo_root)
    try:
        return resolve_opsx_repository_root(candidate)
    except ValueError:
        return candidate.resolve()


def red_test_file_content_identity(repo_root: Path, node_id: str) -> str:
    """SHA-256 байтов тестового файла для canonical pytest node id."""
    normalized = validate_repair_hop_pytest_node_id(node_id)
    file_part = normalized.split("::", 1)[0]
    test_path = (resource_path(repo_root, file_part)).resolve()
    if not test_path.is_file():
        msg = f"test file missing: {test_path}"
        raise FileNotFoundError(msg)
    return hashlib.sha256(test_path.read_bytes()).hexdigest()


def _failure_snippet_accepted(
    actual_failure: str | None,
    tail: str,
) -> bool:
    """Грубая сверка: ключевая строка из actual_failure есть в хвосте pytest."""
    if not actual_failure or not tail:
        return False
    first_line = ""
    for line in actual_failure.splitlines():
        stripped = line.strip()
        if stripped:
            first_line = stripped
            break
    if not first_line:
        return False
    if first_line in tail:
        return True
    if len(first_line) > 40 and first_line[:40] in tail:
        return True
    return False


def validate_bug_team_red_operative(
    evidence: RedEvidence,
    *,
    repo_root: Path | str,
) -> RedOperativeValidation:
    """Подтвердить RED_CONFIRMED: pytest exit 1, не env/collection/tool error."""
    if evidence.status != "RED_CONFIRMED":
        return RedOperativeValidation(ok=True, test_content_identity=None, errors=[])

    errors: list[str] = []
    test_id = str(evidence.test_id or "").strip()
    if not test_id:
        return RedOperativeValidation(
            ok=False,
            test_content_identity=None,
            errors=["RED_OPERATIVE_MISSING_TEST_ID"],
        )

    try:
        node_id = validate_repair_hop_pytest_node_id(test_id)
    except ValueError as exc:
        return RedOperativeValidation(
            ok=False,
            test_content_identity=None,
            errors=[f"RED_OPERATIVE_INVALID_TEST_ID:{exc}"],
        )

    root = _resolve_repair_repo_root(repo_root)
    try:
        result = run_repair_hop_pytest_sync(root, node_id)
    except Exception as exc:
        return RedOperativeValidation(
            ok=False,
            test_content_identity=None,
            errors=[f"RED_OPERATIVE_PYTEST_RUN_FAILED:{type(exc).__name__}:{exc}"],
            pytest_result=None,
        )

    if result.get("code") in {"INVALID_ARGS", "INVALID_NODE_ID", "PYTEST_RUN_FAILED"}:
        code = str(result.get("code") or "PYTEST_RUN_FAILED")
        errors.append(f"RED_OPERATIVE_{code}")
        return RedOperativeValidation(
            ok=False,
            test_content_identity=None,
            errors=errors,
            pytest_result=result,
        )

    exit_code = int(result.get("exit_code", -1))
    tail = str(result.get("tail") or "")

    if _COLLECTION_MARKERS.search(tail):
        errors.append("RED_OPERATIVE_COLLECTION_ERROR")
    if exit_code == 0:
        errors.append("RED_OPERATIVE_TEST_PASSED_NOT_RED")
    elif exit_code not in {1}:
        errors.append(f"RED_OPERATIVE_UNEXPECTED_EXIT:{exit_code}")

    if not _failure_snippet_accepted(evidence.actual_failure, tail):
        errors.append("RED_OPERATIVE_FAILURE_NOT_IN_PYTEST_OUTPUT")

    if errors:
        return RedOperativeValidation(
            ok=False,
            test_content_identity=None,
            errors=errors,
            pytest_result=result,
        )

    try:
        identity = red_test_file_content_identity(root, node_id)
    except OSError as exc:
        return RedOperativeValidation(
            ok=False,
            test_content_identity=None,
            errors=[f"RED_OPERATIVE_TEST_IDENTITY:{exc}"],
            pytest_result=result,
        )

    return RedOperativeValidation(
        ok=True,
        test_content_identity=identity,
        errors=[],
        pytest_result=result,
    )


def validate_bug_team_green_operative(
    green: GreenEvidence,
    *,
    repo_root: Path | str,
    red_test_id: str | None,
) -> list[str]:
    """Подтвердить GREEN_CONFIRMED: authoritative test exit 0."""
    model = green
    if model.status != "GREEN_CONFIRMED":
        return []

    errors: list[str] = []
    test_id = str(model.test_id or red_test_id or "").strip()
    if not test_id:
        errors.append("GREEN_OPERATIVE_MISSING_TEST_ID")
        return errors

    try:
        node_id = validate_repair_hop_pytest_node_id(test_id)
    except ValueError as exc:
        errors.append(f"GREEN_OPERATIVE_INVALID_TEST_ID:{exc}")
        return errors

    root = _resolve_repair_repo_root(repo_root)
    try:
        result = run_repair_hop_pytest_sync(root, node_id)
    except Exception as exc:
        errors.append(f"GREEN_OPERATIVE_PYTEST_RUN_FAILED:{type(exc).__name__}:{exc}")
        return errors

    if result.get("code") in {"INVALID_ARGS", "INVALID_NODE_ID", "PYTEST_RUN_FAILED"}:
        code = str(result.get("code") or "PYTEST_RUN_FAILED")
        errors.append(f"GREEN_OPERATIVE_{code}")
        return errors

    exit_code = int(result.get("exit_code", -1))
    if exit_code != 0:
        errors.append(f"GREEN_OPERATIVE_TEST_NOT_GREEN:{exit_code}")
    return errors
