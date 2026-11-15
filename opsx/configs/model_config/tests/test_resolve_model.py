"""Тесты resolve_model."""

from model_config.agent_roles import AgentRole
from model_config.bug_bot_model import low_model, medium_model
from model_config.model_presets import DEFAULT_HOP_MODEL, composer_standart
from model_config.models.composer import Composer
from model_config.models.grok_47 import Grok47
from model_config.resolve_model import (
    ModelResolveRequest,
    ModelResolveWorkflow,
    resolve_model,
)


def test_default_uses_composer_standart_preset():
    result = resolve_model(
        ModelResolveRequest(role=AgentRole.BOUNDED_HUNTER, mission_id="m-1")
    )
    assert isinstance(result.model, Composer)
    assert result.model is DEFAULT_HOP_MODEL
    assert result.model is composer_standart
    assert result.model.name == "composer-2.5"
    assert result.model.fast is False


def test_search_rounds_circle_1_uses_low_preset():
    result = resolve_model(
        ModelResolveRequest(
            role=AgentRole.BOUNDED_HUNTER,
            mission_id="m-1",
            workflow=ModelResolveWorkflow.SEARCH_ROUNDS,
            round=1,
        )
    )
    assert isinstance(result.model, Composer)
    assert result.model is low_model is DEFAULT_HOP_MODEL


def test_search_rounds_circle_2_uses_grok_medium():
    result = resolve_model(
        ModelResolveRequest(
            role=AgentRole.BOUNDED_HUNTER,
            mission_id="m-1",
            workflow=ModelResolveWorkflow.SEARCH_ROUNDS,
            round=2,
        )
    )
    assert isinstance(result.model, Grok47)
    assert result.model is medium_model
