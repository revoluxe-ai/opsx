"""Какие сценарии ещё без канонического теста."""
from __future__ import annotations

from opsx.project_context import resource_path

from pathlib import Path

from agents.common.scenario_files import read_scenario_ids, scenario_test_relpath
from agents.common.scenario_migrate import docstring_satisfies
from agents.repair.scenario_test_repair.workflow_pytest_defer import (
    _discover_mapped_node,
    validate_mapped_workflow_deferred_marker,
)


def row_needs_authoring(repo: Path, change_id: str, scenario_id: str) -> bool:
    """True, если нет файла или в test-функции нет контрактного ``SCENARIO_ID``."""
    target = resource_path(repo, scenario_test_relpath(change_id, scenario_id))
    if not target.is_file():
        return True
    try:
        source = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return True
    if not docstring_satisfies(source, scenario_id):
        return True
    mapped = _discover_mapped_node(repo, change_id, scenario_id)
    if mapped and validate_mapped_workflow_deferred_marker(repo, mapped) is not None:
        return True
    return False


def scenarios_needing_authoring(repo: Path, change_id: str) -> list[str]:
    """Сценарии, для которых ещё нужен SDK hop."""
    coverage = resource_path(repo, "openspec", "changes", change_id, "scenario-coverage")
    try:
        scenario_ids = read_scenario_ids(coverage, allow_historical=False)
    except ValueError:
        return []
    return [sid for sid in scenario_ids if row_needs_authoring(repo, change_id, sid)]
