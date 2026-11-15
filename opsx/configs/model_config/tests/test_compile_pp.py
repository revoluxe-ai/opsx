"""Тесты compile_pp."""

import pytest

from model_config.agent_roles import AgentRole
from model_config.bug_bot_model import high_model, low_model, medium_model
from model_config.compile_pp import DEFAULT_BUGBOT_VERSION, BugBotVersion, compile_pp
from model_config.model_presets import super_low_model


def test_compile_pp_default_matches_current_presets():
    compiled = compile_pp()
    assert compiled.super_low is super_low_model
    assert compiled.search_round_low is low_model
    assert compiled.search_round_medium is medium_model
    assert compiled.search_round_high is high_model


def test_bugbot_version_proxies_base_agent_role():
    version = BugBotVersion()
    assert version.role is AgentRole.BUG_BOT
    assert compile_pp(version).search_round_low is low_model
    assert compile_pp(DEFAULT_BUGBOT_VERSION).super_low is super_low_model


def test_compile_pp_rejects_non_bugbot_role():
    broken = BugBotVersion(role=AgentRole.BOUNDED_HUNTER)
    with pytest.raises(ValueError, match="expected base bugbot agent"):
        compile_pp(broken)
