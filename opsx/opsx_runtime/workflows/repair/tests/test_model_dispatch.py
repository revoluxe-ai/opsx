"""Тест model_dispatch → resolve."""

from __future__ import annotations

from workflows.repair.model_dispatch import (
    resolve_model_for_role,
    search_model_round,
)
from model_config.agent_roles import AgentRole
from model_config.bug_bot_model import low_model, medium_model
from model_config.model_presets import DEFAULT_HOP_MODEL
from model_config.models.composer import Composer
from model_config.models.grok_47 import Grok47
from agents.adapters.hop_model_selection import model_selection_for_hop
from workflows.repair.schemas import HuntPayload, ModelSelection, RepairState

SEARCH_MODEL_MAX_ROUND = 3


def test_execute_role_path_uses_resolve_not_state_model(tmp_path):
    state = RepairState(
        mission_id="m1",
        repository_path=str(tmp_path),
        workspace=str(tmp_path),
        output_dir=str(tmp_path / "out"),
        model=ModelSelection(id="gpt-5.4-nano", params=[]),
    )
    result = resolve_model_for_role(
        state, role=AgentRole.GREEN_VERIFIER, hop="008-green"
    )
    sdk = model_selection_for_hop(result)
    assert sdk["id"] == "composer-2.5"
    param_map = {p["id"]: p["value"] for p in sdk["params"]}
    assert param_map["fast"] == "false"
    assert result.model is DEFAULT_HOP_MODEL


def test_search_model_round_first_hunt_is_one():
    state = RepairState(
        mission_id="m1",
        repository_path=".",
        workspace=".",
        output_dir=".",
    )
    assert search_model_round(state) == 1


def test_search_model_round_after_clear_escalates():
    state = RepairState(
        mission_id="m1",
        repository_path=".",
        workspace=".",
        output_dir=".",
        hunts=[HuntPayload(hunt_result="CLEAR")],
    )
    assert search_model_round(state) == 2


def test_search_model_round_caps_at_three():
    state = RepairState(
        mission_id="m1",
        repository_path=".",
        workspace=".",
        output_dir=".",
        hunts=[HuntPayload(hunt_result="CLEAR")] * 8,
    )
    assert search_model_round(state) == SEARCH_MODEL_MAX_ROUND


def test_bounded_hunter_after_clear_uses_medium(tmp_path):
    state = RepairState(
        mission_id="m1",
        repository_path=str(tmp_path),
        workspace=str(tmp_path),
        output_dir=str(tmp_path / "out"),
        model=ModelSelection(id="gpt-5.4-nano", params=[]),
        hunts=[HuntPayload(hunt_result="CLEAR")],
    )
    result = resolve_model_for_role(
        state, role=AgentRole.BOUNDED_HUNTER, hop="010-hunt"
    )
    assert isinstance(result.model, Grok47)
    assert result.model is medium_model


def test_bounded_hunter_first_pass_uses_low(tmp_path):
    state = RepairState(
        mission_id="m1",
        repository_path=str(tmp_path),
        workspace=str(tmp_path),
        output_dir=str(tmp_path / "out"),
        model=ModelSelection(id="gpt-5.4-nano", params=[]),
    )
    result = resolve_model_for_role(
        state, role=AgentRole.BOUNDED_HUNTER, hop="010-hunt"
    )
    assert isinstance(result.model, Composer)
    assert result.model is low_model
