"""Git commit после зелёной (SUPPORTED) строки scenario row walk."""

from __future__ import annotations

from opsx.project_context import resource_path

from pathlib import Path
from typing import Any

from agents.adapters.abc_adapter import ABCAdapter
from agents.common.commit_agent import CommitAgent
from agents.verify.scenario_coverage_verify.row_receipts import row_check, row_key
from workflows.implementation.node_subprocess import run_subprocess_utf8
from workflows.repair.schemas import FixEvidence, ModelSelection, RepairState


def _git_tracks_path(repo: Path, rel: str) -> bool:
    """True, если путь есть в индексе или рабочем дереве git (включая удалённый файл)."""
    proc = run_subprocess_utf8(
        ["git", "-C", str(repo), "ls-files", "--error-unmatch", "--", rel],
    )
    return proc.returncode == 0


def collect_green_row_commit_paths(
    repo: Path,
    change_id: str,
    scenario_id: str,
    *,
    extra_paths: list[str],
) -> list[str]:
    """Собрать пути артефактов одной зелёной строки, существующие в репозитории."""
    coverage = resource_path(repo, "openspec", "changes", change_id, "scenario-coverage")
    candidates: list[str] = []
    from agents.common.scenario_files import row_walk_journal_machine_path

    journal = row_walk_journal_machine_path(coverage)
    if journal.is_file():
        candidates.append(journal.relative_to(repo).as_posix())
    obs = (
        resource_path(repo, "openspec", "changes", change_id, "verify", "scenario-rows", f"{row_key(scenario_id)}.json")
    )
    if obs.is_file():
        candidates.append(obs.relative_to(repo).as_posix())
    receipt = (
        resource_path(repo, "openspec", "changes", change_id, "verify", "check-receipts", f"{row_check(scenario_id)}.json")
    )
    if receipt.is_file():
        candidates.append(receipt.relative_to(repo).as_posix())
    paths: list[str] = []
    for path in candidates:
        if path and (resource_path(repo, path)).is_file():
            paths.append(path)
    for path in extra_paths:
        rel = str(path).strip()
        if not rel or rel in paths:
            continue
        if (resource_path(repo, rel)).is_file() or _git_tracks_path(repo, rel):
            paths.append(rel)
    return list(dict.fromkeys(paths))


def green_row_commit_message_task(change_id: str, scenario_id: str) -> str:
    """Текст задачи для ``CommitAgent`` при коммите одной SUPPORTED-строки."""
    return (
        f"Закоммить результат зелёной проверки scenario coverage "
        f"(change={change_id}, scenario_id={scenario_id}): "
        "observation, supported receipt, журнал row_walk и изменённые тесты/matrix."
    )


async def commit_green_scenario_row(
    state: dict[str, Any],
    *,
    repo: Path,
    change_id: str,
    scenario_id: str,
    extra_paths: list[str],
    commit_adapter: ABCAdapter | None,
) -> dict[str, Any] | None:
    """Создать локальный commit для одной SUPPORTED-строки; ``None`` если нечего коммитить."""
    files = collect_green_row_commit_paths(
        repo, change_id, scenario_id, extra_paths=extra_paths,
    )
    if not files:
        return None
    if not (resource_path(repo, ".git")).is_dir():
        return {"commit_status": "SKIPPED_NO_GIT", "commit_sha": None, "commit_files": files}

    model_raw = state.get("model")
    model = (
        ModelSelection.model_validate(model_raw)
        if isinstance(model_raw, dict)
        else ModelSelection()
    )
    workspace = str(state.get("workspace") or state.get("repository_path") or repo)
    key = row_key(scenario_id)[:12]
    repair_state = RepairState(
        mission_id=f"scenario-row-{change_id}-{key}",
        repository_path=str(repo),
        workspace=workspace,
        output_dir=str(
            resource_path(repo, "opsx", "runs", "verify", "scenario-row-commit", change_id, key),
        ),
        model=model,
        status="ACTIVE",
        current_stage="fix",
        owner_go=True,
        commit_message_task=green_row_commit_message_task(change_id, scenario_id),
        fix=FixEvidence(
            status="FIXED",
            summary=f"scenario row SUPPORTED: {scenario_id}",
            files_changed=files,
            notes=scenario_id,
        ),
    )
    agent = CommitAgent(adapter=commit_adapter, allow_empty=False)
    return await agent.run(repair_state)
