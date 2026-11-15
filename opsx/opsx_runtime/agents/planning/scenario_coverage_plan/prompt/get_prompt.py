"""Промпт mode=plan без slash-команды и без audit/certify веток."""

from __future__ import annotations

from opsx.project_context import resource_path

import json
from pathlib import Path
from typing import Any

from agents.common.prompt_embed import bounded_json_block

_PROMPT_DIR = Path(__file__).resolve().parent
_PLAN_TEMPLATE_FILE = "plan_template.md"
_SCENARIO_FILE_TEMPLATE = "scenario_file_template.md"

_ANTI_SCAN = (
    "Do NOT use repo-root grep/glob/ls to discover context. "
    "INPUT CONTEXT and PLAN TEMPLATES below are complete for planning.\n"
    "Do NOT read `scenario-coverage/plan.md` from **other** changes — use templates only.\n"
    "Do NOT read or write `opsx/receipts/**`, `.cursor/**`, `opsx_tools/**`, "
    "`agent-transcripts/**`.\n"
)


def _load_template(filename: str, *, change_id: str) -> str:
    """Прочитать repo-local шаблон plan/scenario и подставить CHANGE_ID."""
    path = _PROMPT_DIR / filename
    return path.read_text(encoding="utf-8").replace("<CHANGE_ID>", change_id)


def _existing_plan_embed(change_coverage: Path) -> str:
    """Встроить текущий plan.md этого change целиком (без read hop модели)."""
    plan = change_coverage / "plan.md"
    if not plan.is_file():
        return ""
    text = plan.read_text(encoding="utf-8", errors="replace")
    return (
        "## EXISTING plan.md (this change — update in place if inputs changed)\n\n"
        f"```\n{text}\n```\n\n"
    )


async def get_prompt(
    *,
    repository_path: str,
    change_id: str,
    artifact_bundle: dict[str, Any] | None = None,
) -> str:
    """Собрать инструкцию для durable scenario-coverage plan."""
    root = Path(repository_path)
    change = resource_path(root, "openspec", "changes", change_id)
    coverage = change / "scenario-coverage"
    bundle_json = bounded_json_block(artifact_bundle or {}, max_chars=480_000)
    plan_template = _load_template(_PLAN_TEMPLATE_FILE, change_id=change_id)
    scenario_template = _load_template(_SCENARIO_FILE_TEMPLATE, change_id=change_id)
    existing_plan = _existing_plan_embed(coverage)
    return (
        "# Scenario coverage plan (mode=plan)\n\n"
        f"Repository: {repository_path}\n"
        f"Change: {change_id}\n\n"
        f"{_ANTI_SCAN}\n"
        "## INPUT CONTEXT (embedded — do not re-read)\n\n"
        f"```json\n{bundle_json}\n```\n\n"
        "## PLAN FILE TEMPLATE (canonical plan.md layout)\n\n"
        f"```\n{plan_template}\n```\n\n"
        "## SCENARIO FILE TEMPLATE (one scenarios/<sha256>.md per SCENARIO_ID)\n\n"
        f"```\n{scenario_template}\n```\n\n"
        f"{existing_plan}"
        "Write ONLY durable plan evidence under:\n"
        f"- {coverage / 'plan.md'}\n"
        f"- {coverage / 'scenarios'}/*.md\n\n"
        "Do NOT edit design.md, specs, tasks, owner-contract, tests, or test_trace_matrix.json.\n"
        "Do NOT write `plan.receipt.json` — runtime writes machine receipts.\n"
        "In your JSON answer, set status to PLAN_READY | PLAN_PARTIAL | BLOCKED and "
        "`scenario_count` to the number of scenario files you wrote.\n"
        "Do not embed SCENARIO_ID bodies inside plan.md (machine gate COVERAGE_PLAN_EMBEDDED).\n"
    )
