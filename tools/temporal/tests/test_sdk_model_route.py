"""Fail-closed contract tests for the SDK model-route translation boundary.

Covers: legacy aliases never reach the SDK as model_id, unknown selectors
and bare base ids fail closed, exact base id + validated params are emitted,
no fallback model is selected, and parameter ids are never guessed across
model families (Luna uses reasoning/context/fast; Grok uses effort/fast).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GAME_ROOT = ROOT.parents[1]
CURSOR_EXECUTOR_ROOT = GAME_ROOT / "tools" / "cursor_executor"
for path in (str(ROOT), str(CURSOR_EXECUTOR_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from cursor_executor.preflight import validate_model_route  # noqa: E402
from cursor_executor.schemas import ModelRoute  # noqa: E402
from model_config import AgentModelConfig  # noqa: E402
from sdk_model_route import (  # noqa: E402
    resolve_legacy_selector,
    resolve_sdk_model_route,
)


def _live_shaped_catalog() -> list[dict]:
    """Fake catalog mirroring the live SDK parameter contract."""
    return [
        {
            "id": "gpt-5.6-luna",
            "displayName": "GPT-5.6 Luna",
            "parameters": [
                {"id": "context", "values": [{"value": "272k"}, {"value": "1m"}]},
                {
                    "id": "reasoning",
                    "values": [{"value": v} for v in ("none", "low", "medium", "high", "xhigh", "max")],
                },
                {"id": "fast", "values": [{"value": "false"}, {"value": "true"}]},
            ],
        },
        {
            "id": "grok-4.6",
            "displayName": "Cursor Grok 4.6",
            "parameters": [
                {
                    "id": "effort",
                    "values": [{"value": v} for v in ("low", "medium", "high", "xhigh")],
                },
                {"id": "fast", "values": [{"value": "false"}, {"value": "true"}]},
            ],
        },
        {
            "id": "composer-2.5",
            "displayName": "Composer 2.5",
            "parameters": [
                {"id": "fast", "values": [{"value": "false"}, {"value": "true"}]},
            ],
        },
        {
            "id": "gpt-5.6-sol",
            "displayName": "GPT-5.6 Sol",
            "parameters": [
                {"id": "context", "values": [{"value": "272k"}, {"value": "1m"}]},
                {
                    "id": "reasoning",
                    "values": [{"value": v} for v in ("none", "low", "medium", "high", "xhigh", "max")],
                },
                {"id": "fast", "values": [{"value": "false"}, {"value": "true"}]},
            ],
        },
    ]


def _assert_valid(route: ModelRoute) -> None:
    validation = validate_model_route(route, _live_shaped_catalog())
    assert hasattr(validation, "validated_route"), f"route rejected: {validation!r}"


def _synthetic_config(model: str) -> AgentModelConfig:
    return AgentModelConfig(
        {
            "version": 1,
            "default_tier": "custom",
            "steps": {"review": "custom"},
            "tiers": {
                "custom": {
                    "label": "synthetic",
                    "model": model,
                    "reasoning": "auto",
                    "speed": "standard",
                    "cli_model": model,
                }
            },
        }
    )


def test_medium_tier_routes_through_cli_model_for_sdk():
    cfg = AgentModelConfig.load().with_steps(review="medium", repair="medium")
    route = resolve_sdk_model_route(cfg, step="review")
    assert route == ModelRoute("composer-2.5", {})
    _assert_valid(route)


def test_high_tier_routes_through_cli_model_for_sdk():
    cfg = AgentModelConfig.load().with_steps(review="high", repair="high")
    route = resolve_sdk_model_route(cfg, step="repair")
    assert route == ModelRoute("composer-2.5", {})
    _assert_valid(route)


def test_luna_route_has_no_grok_params():
    route = resolve_legacy_selector("gpt-5.6-luna-xhigh")
    assert "effort" not in route.params
    assert route.params["reasoning"] == "xhigh"


def test_composer_low_tier_exact():
    cfg = AgentModelConfig.load().with_steps(review="low", repair="low")
    route = resolve_sdk_model_route(cfg, step="review")
    assert route == ModelRoute("composer-2.5", {})
    _assert_valid(route)


def test_unknown_selector_fails_closed_no_fallback():
    with pytest.raises(ValueError):
        resolve_legacy_selector("gpt-999-nope")
    with pytest.raises(ValueError):
        resolve_sdk_model_route(_synthetic_config("gpt-999-nope"), step="review")
    with pytest.raises(ValueError):
        resolve_sdk_model_route(_synthetic_config("cursor-grok-4.6-ultra"), step="review")


def test_bare_base_id_without_params_fails_closed():
    # Bare base ids carry no explicit params -> unsupported tier spec.
    with pytest.raises(ValueError):
        resolve_legacy_selector("grok-4.6")
    with pytest.raises(ValueError):
        resolve_legacy_selector("gpt-5.6-luna")
    with pytest.raises(ValueError):
        resolve_sdk_model_route(_synthetic_config("grok-4.6"), step="review")


def test_unsupported_tier_name_fails_closed():
    cfg = AgentModelConfig.load()
    with pytest.raises(KeyError):
        cfg.get_tier("ultra")
    with pytest.raises(ValueError):
        resolve_sdk_model_route(_synthetic_config(""), step="review")


def test_fast_speed_variants_explicit():
    luna_fast = resolve_legacy_selector("gpt-5.6-luna-xhigh-fast")
    assert luna_fast == ModelRoute(
        "gpt-5.6-luna", {"context": "272k", "reasoning": "xhigh", "fast": "true"}
    )
    _assert_valid(luna_fast)
    grok_fast = resolve_legacy_selector("cursor-grok-4.6-medium-fast")
    assert grok_fast == ModelRoute("grok-4.6", {"effort": "medium", "fast": "true"})
    assert "reasoning" not in grok_fast.params
    _assert_valid(grok_fast)


@pytest.mark.parametrize("tier", ["low", "medium", "high"])
@pytest.mark.parametrize("step", ["review", "repair", "next_check", "verification_executor"])
def test_all_active_tiers_resolve_and_validate(tier: str, step: str):
    cfg = AgentModelConfig.load().with_steps(review=tier, repair=tier)
    route = resolve_sdk_model_route(cfg, step=step)
    assert route.model_id == "composer-2.5"
    assert "gpt-5.6-luna-xhigh" != route.model_id
    assert "cursor-grok-4.6-medium" != route.model_id
    _assert_valid(route)
