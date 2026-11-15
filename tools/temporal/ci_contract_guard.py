"""Temporal CI contract guard: fail closed if mandatory tests are missing or not collected.

Вызывается из GitHub Actions перед запуском P0-бандла pytest. Не выполняет live SDK,
Cursor API или продуктовые мутации — только ``pytest --collect-only`` и сверку манифеста.
"""

from __future__ import annotations

from opsx.project_context import resource_path, project_root

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = project_root()
_TEMPORAL_ROOT = Path(__file__).resolve().parent

# P0: WorkflowEnvironment, repair transition contract, parser/repair/operator regressions.
REQUIRED_TEST_REL_PATHS: tuple[str, ...] = (
    "tools/temporal/tests/test_workflow_repair_transition_contract.py",
    "tools/temporal/tests/test_temporal_workflow.py",
    "tools/temporal/tests/test_repair_partial_complete.py",
    "tools/temporal/tests/test_pipeline_operator_status.py",
    "tools/temporal/tests/test_repair_progress_evidence.py",
    "tools/temporal/tests/test_project_bug_hunt_markdown_parser.py",
    "tools/temporal/tests/test_parser_blocked_review.py",
)

# Минимум собранных тестов в бандле (пустая коллекция ≠ PASS).
MIN_TOTAL_COLLECTED = 25

# Обязательные node id (суффикс после ``::``), включая parametrized варианты по префиксу.
REQUIRED_NODE_SUFFIXES: tuple[str, ...] = (
    "test_workflow_repair_transition_contract.py::test_classify_completed_repair_has_no_parsers_import",
    "test_workflow_repair_transition_contract.py::test_bug_batch_clear_yes_authorizes_materialize",
    "test_workflow_repair_transition_contract.py::test_non_success_yes_never_authorizes_materialize",
    "test_temporal_workflow.py::test_temporal_workflow_exposes_granular_activities",
    "test_temporal_workflow.py::test_workflow_non_success_repair_skips_materialize_and_rehunt",
    "test_pipeline_operator_status.py",
    "test_project_bug_hunt_markdown_parser.py",
    "test_repair_partial_complete.py",
    "test_parser_blocked_review.py",
)


@dataclass(frozen=True)
class CollectionReport:
    """Результат ``pytest --collect-only`` для Temporal P0-бандла."""

    node_ids: tuple[str, ...]
    exit_code: int
    stderr: str


def _temporal_pythonpath() -> str:
    existing = os.environ.get("PYTHONPATH", "")
    root = str(_TEMPORAL_ROOT)
    if not existing:
        return root
    parts = existing.split(os.pathsep)
    if root in parts:
        return existing
    return os.pathsep.join([root, existing])


def collect_temporal_p0_node_ids() -> CollectionReport:
    """Собирает node id обязательного pytest-бандла через ``pytest --collect-only``."""
    env = {**os.environ, "PYTHONPATH": _temporal_pythonpath()}
    rel_paths = [str(resource_path(_REPO_ROOT, p)) for p in REQUIRED_TEST_REL_PATHS]
    for rel in REQUIRED_TEST_REL_PATHS:
        path = resource_path(_REPO_ROOT, rel)
        if not path.is_file():
            return CollectionReport((), 2, f"missing required test file: {rel}")

    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "--collect-only",
        "-q",
        *rel_paths,
    ]
    proc = subprocess.run(
        cmd,
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    node_ids: list[str] = []
    for line in combined.splitlines():
        stripped = line.strip()
        if stripped.startswith("<") and " not found" in stripped:
            continue
        if "::" in stripped and stripped.endswith(".py"):
            continue
        if "::" in stripped and not stripped.startswith("="):
            # ``tools/temporal/tests/test_foo.py::test_bar``
            if stripped.split()[0].count("::") >= 1:
                node_ids.append(stripped.split()[0])

    # Fallback: pytest may list as ``<Module test_foo.py>`` — parse ``test session starts`` block
    if not node_ids:
        for line in combined.splitlines():
            if "::test_" in line:
                token = line.strip().split()[0]
                if "::" in token:
                    node_ids.append(token)

    unique = tuple(dict.fromkeys(node_ids))
    return CollectionReport(unique, proc.returncode, proc.stderr or proc.stdout or "")


def _normalize_node_id(node_id: str) -> str:
    """Приводит путь к виду ``.../tests/<file>::<test>`` для сравнения."""
    normalized = node_id.replace("\\", "/")
    if "/tools/temporal/tests/" in normalized:
        idx = normalized.index("/tools/temporal/tests/")
        return normalized[idx + 1 :]
    if normalized.startswith("tools/temporal/tests/"):
        return normalized
    parts = normalized.split("/")
    if "tests" in parts:
        idx = parts.index("tests")
        return "/".join(parts[idx - 1 :] if idx > 0 else parts[idx:])
    return normalized


def verify_collection(
    node_ids: tuple[str, ...],
    *,
    required_suffixes: tuple[str, ...] = REQUIRED_NODE_SUFFIXES,
    min_total: int = MIN_TOTAL_COLLECTED,
) -> list[str]:
    """Возвращает список ошибок; пустой список — контракт выполнен."""
    errors: list[str] = []
    if not node_ids:
        errors.append(
            "pytest collection returned zero tests for Temporal P0 bundle (fail closed)"
        )
        return errors

    normalized = tuple(_normalize_node_id(n) for n in node_ids)
    if len(normalized) < min_total:
        errors.append(
            f"collected {len(normalized)} tests, minimum required is {min_total}"
        )

    for suffix in required_suffixes:
        if "::" in suffix and suffix.endswith(".py") is False:
            if not any(n.endswith(suffix) or suffix in n for n in normalized):
                errors.append(f"required test node not collected: {suffix}")
        else:
            # file-level: at least one test from module
            file_part = suffix if suffix.endswith(".py") else suffix
            if not any(file_part in n for n in normalized):
                errors.append(f"required test module not collected: {file_part}")

    return errors


def collection_process_errors(report: CollectionReport) -> list[str]:
    """Ошибки процесса collect-only; ненулевой exit code всегда fail closed."""
    if report.exit_code == 0:
        return []
    return [
        "pytest --collect-only exited with code "
        f"{report.exit_code} (fail closed even when partial nodes were parsed)"
    ]


def main(argv: list[str] | None = None) -> int:
    """CLI: ``verify-collection`` или ``print-manifest``."""
    parser = argparse.ArgumentParser(description="Temporal CI contract guard")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("verify-collection", help="Run collect-only and enforce manifest")
    sub.add_parser("print-manifest", help="Emit JSON manifest for operators")
    args = parser.parse_args(argv)

    if args.command == "print-manifest":
        payload = {
            "required_test_paths": list(REQUIRED_TEST_REL_PATHS),
            "min_total_collected": MIN_TOTAL_COLLECTED,
            "required_node_suffixes": list(REQUIRED_NODE_SUFFIXES),
        }
        print(json.dumps(payload, indent=2))
        return 0

    report = collect_temporal_p0_node_ids()
    errors = collection_process_errors(report)
    if report.exit_code != 0:
        if report.stderr:
            print(report.stderr, file=sys.stderr)
        print(
            f"pytest --collect-only failed with exit code {report.exit_code}",
            file=sys.stderr,
        )
        if report.node_ids:
            print(
                f"partial collection parsed {len(report.node_ids)} node id(s); "
                "guard still fails closed",
                file=sys.stderr,
            )

    errors.extend(verify_collection(report.node_ids))
    if errors:
        print("Temporal CI contract guard FAILED:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        print(f"collected node ids ({len(report.node_ids)}):", file=sys.stderr)
        for node_id in report.node_ids[:50]:
            print(f"    {node_id}", file=sys.stderr)
        if len(report.node_ids) > 50:
            print(f"    ... and {len(report.node_ids) - 50} more", file=sys.stderr)
        return 1

    print(
        f"Temporal CI contract guard OK: {len(report.node_ids)} tests collected "
        f"from {len(REQUIRED_TEST_REL_PATHS)} modules"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
