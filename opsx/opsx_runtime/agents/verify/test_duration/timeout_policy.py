"""Персональный pytest-timeout: потолок, маркер и классификация stall/timeout."""

from __future__ import annotations

from opsx.project_context import resource_path

import json
import math
import re
from pathlib import Path
from typing import Any, Literal

from workflows.verify.gate_repair.pytest_gate_timeout import QUALITY_GATE_PER_TEST_SECONDS

PERSONAL_TIMEOUT_CEILING_SECONDS = 300
_TIMEOUT_MARK = re.compile(r"@pytest\.mark\.timeout\(\s*(\d+(?:\.\d+)?)\s*\)")
_DURATION_TOKENS = ("PYTEST_STALL", "PYTEST_SUITE_TIMEOUT", "Timeout")


def is_pytest_duration_text(text: str) -> bool:
    """Строка pytest Timeout / stall / suite-timeout, не обычный assert."""
    return any(token in text for token in _DURATION_TOKENS)


def clamp_personal_timeout_seconds(seconds: float) -> int:
    """Маркер выше 300 с в рантайме считается как 300 и зависание не отключает."""
    bounded = min(float(PERSONAL_TIMEOUT_CEILING_SECONDS), max(1.0, seconds))
    return int(round(bounded))


def personal_timeout_extra_seconds(repo_root: Path) -> float:
    """Сумма ``max(0, N - per_test)`` по маркерам для запаса subprocess backstop."""
    tests_dir = resource_path(repo_root, "tests")
    if not tests_dir.is_dir():
        return 0.0
    extra = 0.0
    for path in tests_dir.rglob("test_*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in _TIMEOUT_MARK.finditer(text):
            seconds = clamp_personal_timeout_seconds(float(match.group(1)))
            extra += max(0.0, seconds - QUALITY_GATE_PER_TEST_SECONDS)
    return extra


def measured_personal_timeout_seconds(elapsed_seconds: float) -> int:
    """Измеренное время с запасом, не ниже дефолта и не выше потолка."""
    padded = max(QUALITY_GATE_PER_TEST_SECONDS + 1.0, math.ceil(elapsed_seconds * 1.25))
    return clamp_personal_timeout_seconds(padded)


def write_pytest_timeout_marker(repo_root: Path, node_id: str, seconds: int) -> bool:
    """Поставить ``@pytest.mark.timeout(N)`` на функцию node id. ``False``, если функцию не нашли."""
    relative, _, func_name = node_id.partition("::")
    if not relative.endswith(".py") or not func_name:
        return False
    func_name = func_name.split("::")[-1]
    path = resource_path(repo_root, relative.replace("\\", "/"))
    if not path.is_file():
        return False
    bounded = clamp_personal_timeout_seconds(seconds)
    marker = f"@pytest.mark.timeout({bounded})"
    lines = path.read_text(encoding="utf-8").splitlines()
    def_prefix = f"def {func_name}("
    async_prefix = f"async def {func_name}("
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if not (stripped.startswith(def_prefix) or stripped.startswith(async_prefix)):
            continue
        indent = line[: len(line) - len(stripped)]
        window_start = max(0, index - 6)
        for prev in range(index - 1, window_start - 1, -1):
            if _TIMEOUT_MARK.search(lines[prev]):
                lines[prev] = f"{indent}{marker}"
                _ = path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                return True
        lines.insert(index, f"{indent}{marker}")
        _ = path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True
    return False


def stall_evidence_from_gate_error(error: str | None) -> dict[str, Any] | None:
    """Разобрать JSON stall evidence из ``GateCheckResult.error``."""
    if not error or not error.strip().startswith("{"):
        return None
    try:
        payload = json.loads(error)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if not payload.get("stall_when"):
        return None
    return payload


def hang_product_proven_from_stall_evidence(evidence: dict[str, Any]) -> bool:
    """Продуктовый hang: стек до kill и доказанное неограниченное ожидание в product-кадре.

    Одного кадра в product path недостаточно — нужно поле ``stall_function_analysis``
    с ``unbounded_wait_proven=true`` (read-only разбор исходника при сборе evidence).
    """
    if evidence.get("stack_status") != "captured":
        return False
    if not evidence.get("hang_product_eligible"):
        return False
    if evidence.get("stall_when") != "call":
        return False
    analysis = evidence.get("stall_function_analysis")
    if not isinstance(analysis, dict):
        return False
    return analysis.get("unbounded_wait_proven") is True


def hang_test_proven_from_stall_evidence(evidence: dict[str, Any], node_id: str) -> bool:
    """Зависание в файле теста по стеку до kill."""
    if evidence.get("stack_status") != "captured":
        return False
    stderr = str(evidence.get("stderr_tail") or "")
    if "File " not in stderr and "Stack (most recent call last)" not in stderr:
        return False
    test_path = node_id.partition("::")[0].replace("\\", "/")
    return test_path in stderr


DurationClassification = Literal[
    "HANG_PRODUCT", "HANG_TEST", "PERSONAL_TIMEOUT", "UNCLEAR"
]


def deterministic_duration_classification(
    *,
    observation: str,
    node_id: str = "",
    stall_evidence: dict[str, Any] | None = None,
    personal_timeout_probe: dict[str, Any] | None = None,
    duration_probe_exhausted: bool = False,
) -> DurationClassification | None:
    """Классификация timeout/stall без LLM для harness, probe и доказанного hang.

    ``PYTEST_STALL`` на продуктовом node без доказательства hang **не** даёт ``HANG_PRODUCT``.
    """
    if not is_pytest_duration_text(observation):
        return None
    node = node_id.strip()
    if not node:
        from workflows.verify.gate_repair.pytest_failure_excerpt import (
            node_id_from_pytest_observation,
        )

        node = node_id_from_pytest_observation(observation) or ""
    haystack = f"{node}\n{observation}"
    if "test_pytest_collection_scope" in haystack or node.startswith("opsx/"):
        return "HANG_TEST"

    if personal_timeout_probe and personal_timeout_probe.get("status") == "PASS":
        return "PERSONAL_TIMEOUT"

    if duration_probe_exhausted:
        return "UNCLEAR"

    if stall_evidence:
        if hang_product_proven_from_stall_evidence(stall_evidence):
            return "HANG_PRODUCT"
        if hang_test_proven_from_stall_evidence(stall_evidence, node):
            return "HANG_TEST"
        if "PYTEST_STALL" in observation and stall_evidence.get("stack_status") == "absent":
            return None

    if "PYTEST_STALL" in observation:
        return None

    return None


def duration_probe_key(node_id: str, observation: str) -> str:
    """Ключ одного пробного прогона на неизменный finding."""
    return f"{node_id.strip()}\n{observation.strip()}"


def repair_unit_is_duration_only(unit_item: dict[str, object]) -> bool:
    """Все findings repair unit — timeout/stall, без обычных assert."""
    findings = unit_item.get("findings")
    if not isinstance(findings, list) or not findings:
        observation = unit_item.get("observation")
        return isinstance(observation, str) and is_pytest_duration_text(observation)
    seen = False
    for row in findings:
        if not isinstance(row, dict):
            continue
        seen = True
        observation = str(row.get("observation") or "")
        if not is_pytest_duration_text(observation):
            return False
    return seen
