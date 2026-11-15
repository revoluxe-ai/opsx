"""Unit-тесты level-ladder bugbot/security и skip-маршрутизации verify."""

from __future__ import annotations

from workflows.verify.pipeline_routing import resolve_skip_forward_after
from workflows.verify.verify_check_level import (
    VERIFY_CHECK_MAX_LEVEL,
    patch_advance_verify_check_level,
    patch_verify_check_level_model,
    read_verify_check_level,
    resolve_verify_check_level_model,
)
from workflows.verify.cycle_checks import CYCLE_CHECKS_STATE_KEY, CheckName


def test_verify_check_level_models():
    low = resolve_verify_check_level_model(1)
    medium = resolve_verify_check_level_model(2)
    high = resolve_verify_check_level_model(3)
    assert low.name != medium.name or low != medium
    assert medium.name != high.name or medium != high


def test_patch_verify_check_level_model_uses_level_field():
    state = {"bugbot_level": 1}
    patch = patch_verify_check_level_model(state, "bugbot")
    assert patch["model"]["id"] == resolve_verify_check_level_model(1).name
    legacy = patch_verify_check_level_model({"bugbot_level": 2}, "bugbot")
    assert legacy["model"]["id"] == resolve_verify_check_level_model(1).name


def test_read_verify_check_level_defaults_to_one():
    assert read_verify_check_level({}, "security") == 1


def test_single_active_verify_check_level():
    assert VERIFY_CHECK_MAX_LEVEL == 1
    assert patch_advance_verify_check_level({"bugbot_level": 1}, "bugbot") == {}
    assert read_verify_check_level({"bugbot_level": 3}, "bugbot") == 1


def test_skip_forward_after_gate_repair(tmp_path):
    from workflows.verify.tests.test_green_loop import _init_git_repo

    head = _init_git_repo(tmp_path)
    state = {
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        CYCLE_CHECKS_STATE_KEY: {
            "established": {
                CheckName.PROJECT_BUG.value: head,
            },
        },
    }
    assert resolve_skip_forward_after(state, CheckName.GATE_REPAIR) == "bugbot"
