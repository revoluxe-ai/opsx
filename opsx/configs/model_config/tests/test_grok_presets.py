"""Тесты SDK-маппинга Grok."""

from model_config.model_presets import grok_medium, grok_high
from model_config.models.grok_47 import Grok47


def test_grok_medium_preset():
    assert isinstance(grok_medium, Grok47)
    assert grok_medium.name == "grok-4.7"
    assert grok_medium.context.value == "500k"
    assert grok_medium.reasoning_effort.value == "medium"
    assert grok_medium.fast is False


def test_gtok_high_preset():
    assert isinstance(grok_high, Grok47)
    assert grok_high.context.value == "256k"
    assert grok_high.reasoning_effort.value == "high"
    assert grok_high.fast is False
