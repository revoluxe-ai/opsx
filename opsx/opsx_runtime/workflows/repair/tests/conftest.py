"""Общие фикстуры repair workflow tests."""

from __future__ import annotations

import pytest

from workflows.repair import red_operative_validation as rov
from workflows.repair.red_operative_validation import RedOperativeValidation


@pytest.fixture(autouse=True)
def _bypass_operative_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Integration-тесты графа не запускают live product pytest."""

    def _fake_red(evidence, *, repo_root):
        if evidence.status == "RED_CONFIRMED":
            return RedOperativeValidation(
                ok=True,
                test_content_identity="graph-test-identity",
                errors=[],
            )
        return RedOperativeValidation(ok=True, test_content_identity=None, errors=[])

    monkeypatch.setattr(
        "agents.repair.nodes_repair.validate_bug_team_red_operative",
        _fake_red,
    )
    monkeypatch.setattr(
        "agents.repair.green_verifier.agent.validate_bug_team_green_operative",
        lambda *a, **k: [],
    )
    monkeypatch.setattr(
        "agents.repair.green_verifier.agent.red_test_file_content_identity",
        lambda _root, _node: "graph-test-identity",
    )
