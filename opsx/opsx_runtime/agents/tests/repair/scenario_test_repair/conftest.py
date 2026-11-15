"""Фикстуры offline OPSX-тестов на реальном сохранённом контексте.

Ответы модели не подменяются новыми и live SDK не запускается.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

# Пакет agents лежит в opsx/opsx_runtime и не установлен через pip.
# Для импорта from agents... добавляем его родительскую директорию.
# pytest читает conftest до импорта тестов. Изменение действует только
# для этого поддерева и не трогает общий pythonpath продукта.
# parents[4]: scenario_test_repair/ -> repair/ -> tests/ -> agents/ -> opsx_runtime.
_RUNTIME_ROOT = Path(__file__).resolve().parents[4]
if str(_RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(_RUNTIME_ROOT))

CHANGE_ID = "external-client-notifications-pilot"
RUN_FOLDER = "56736d90f28c"


def pytest_collection_modifyitems(config, items):
    # Даже наличие CURSOR_API_KEY не даёт права вызвать живую модель.
    # Только явный pytest -m live_agent разрешает live-тесты; остальные skip.
    if str(config.option.markexpr or "").strip() == "live_agent":
        return
    skip_live = pytest.mark.skip(
        reason="LIVE_AGENT_DISABLED: use explicit pytest -m live_agent",
    )
    for item in items:
        if item.get_closest_marker("live_agent") is not None:
            item.add_marker(skip_live)


@pytest.fixture(scope="module")
def repo_root() -> Path:
    for path in Path(__file__).resolve().parents:
        if (path / "openspec" / "changes" / CHANGE_ID / "scenario-coverage" / "plan.md").is_file():
            return path
    raise AssertionError("Existing external-client-notifications-pilot source not found")


@pytest.fixture(scope="module")
def recorded_coverage(repo_root: Path) -> dict:
    path = (
        repo_root / "opsx/opsx_runtime/run_dialogs" / CHANGE_ID /
        RUN_FOLDER / "scenario_coverage_verify/conversation.json"
    )
    turns = json.loads(path.read_text(encoding="utf-8"))
    answers = []
    for turn in turns:
        for step in turn.get("turn", {}).get("steps", []):
            message = step.get("message") or {}
            raw = message.get("text") if isinstance(message, dict) else None
            if isinstance(raw, str) and '"row_reviews"' in raw and '"verdict"' in raw:
                try:
                    answers.append(json.loads(raw[raw.index("{"):raw.rindex("}") + 1]))
                except (ValueError, json.JSONDecodeError):
                    continue
    assert answers, "Persisted real ScenarioCoverage audit must exist"
    answer = answers[-1]
    assert answer["verdict"] == "SCENARIO_TEST_COVERAGE_FAIL"
    assert len(answer["gaps"]) == 2  # factual historical failure inventory
    return answer


@pytest.fixture(scope="module")
def recorded_impl(repo_root: Path) -> dict:
    path = repo_root / "openspec" / "changes" / CHANGE_ID / "verify" / "IMPL-VERIFY-RECEIPT.json"
    return json.loads(path.read_text(encoding="utf-8"))["answer"]


@pytest.fixture
def audit_context(repo_root: Path, recorded_coverage: dict, recorded_impl: dict) -> dict:
    """Replay historical semantic payload, bound to CURRENT repo snapshot for fake-SDK tests.

    This is explicitly NOT historical fresh evidence. The rebind makes it
    possible to test preflight and source-gating without invoking a real LLM.
    The original recorded gaps, rows and ImplVerify answer are not rewritten.
    """
    # Import only when offline replay fixture is actually requested.
    # pytest --collect-only -m live_agent must never require adapter runtime.
    from agents.verify.scenario_coverage_verify.evidence_contract import input_snapshot

    digest = input_snapshot(repo_root, CHANGE_ID)["source_digest"]
    audit = {**recorded_coverage, "source_digest": digest}
    run_log = (
        repo_root / "opsx/opsx_runtime/run_dialogs" / CHANGE_ID /
        RUN_FOLDER / "run.log"
    ).read_text(encoding="utf-8")
    models = re.findall(r"agent=apply \| model=([^\s]+)", run_log)
    assert models, "Real Apply model identity must be recoverable from run.log"
    return {
        "model": {"id": models[-1]},
        "change_id": CHANGE_ID,
        "repository_path": str(repo_root),
        "workspace": str(repo_root),
        "run_uuid": RUN_FOLDER,
        "scenario_coverage_execution_id": RUN_FOLDER,
        "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_FAIL",
        "scenario_coverage_answer": audit,
        "scenario_coverage_source_snapshot": {"source_digest": digest},
        "scenario_test_repair_authorized": True,  # fake-SDK test attestation ONLY
        "isolated_workspace": True,  # fake-SDK test attestation ONLY
        "answer": recorded_impl,
    }
