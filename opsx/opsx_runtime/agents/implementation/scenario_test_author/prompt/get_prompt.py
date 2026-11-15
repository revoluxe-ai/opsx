"""Промпт для написания canonical tests/scenarios/<change>/test_<sha>.py."""

from __future__ import annotations

from opsx.project_context import resource_path

from pathlib import Path

from agents.common.prompt_embed import read_bounded_text
from agents.common.scenario_files import scenario_filename


async def get_prompt(
    *,
    repository_path: str,
    change_id: str,
    scenario_ids: list[str],
) -> str:
    """Собрать hop-инструкцию с встроенными телами scenario rows."""
    root = Path(repository_path)
    coverage = resource_path(root, "openspec", "changes", change_id, "scenario-coverage")
    blocks: list[str] = [
        "# Scenario test authoring\n",
        f"Repository: {repository_path}\n",
        f"Change: {change_id}\n",
        "Hop runs immediately after apply-design and before implementation slices.\n",
        f"Write ONLY canonical tests under tests/scenarios/{change_id}/test_<sha256(scenario_id)>.py\n",
        "Each test function body must include SCENARIO_ID inside the function "
        "(not only module level) and scenario docstring markers:\n",
        "Смысл:, Исходные условия:, Действие:, Ожидаемый результат:, SCENARIO_ID:\n",
        "Do NOT modify openspec/, tests/unit/, slice tests, or test_trace_matrix.json.\n",
        "Do NOT grep/glob the repo for context — use embedded scenario bodies below.\n",
        "If the scenario test uses disposable PostgreSQL (fixtures such as "
        "test_database_url, session_factory, db_sessions, or real async PG I/O), "
        "decorate the mapped test function with `@pytest.mark.workflow_deferred` "
        "in addition to any integration/requires_postgres markers. "
        "Hermetic scenario seams under tests/scenarios/ without PG must not add "
        "workflow_deferred.\n",
        "## SCENARIO ROWS (embedded)\n",
    ]
    for scenario_id in scenario_ids:
        rel = coverage / "scenarios" / scenario_filename(scenario_id)
        body = read_bounded_text(rel, max_chars=20_000) if rel.is_file() else None
        blocks.append(f"\n### {scenario_id}\n")
        blocks.append(f"output: tests/scenarios/{change_id}/test_<hash>.py\n")
        if body:
            blocks.append(f"```markdown\n{body}\n```\n")
        else:
            blocks.append(f"(missing file: {rel.relative_to(root)})\n")
    return "".join(blocks)
