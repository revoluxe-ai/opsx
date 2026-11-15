"""Пресет модели ``implementation_arbiter`` → Cursor SDK params."""

from __future__ import annotations

import sys
from pathlib import Path

_CONFIGS = Path(__file__).resolve().parents[2]
if str(_CONFIGS) not in sys.path:
    sys.path.insert(0, str(_CONFIGS))

from model_config.model_presets import IMPLEMENTATION_ARBITER_MODEL  # noqa: E402
from model_config.resolve_model import ModelResolveResult  # noqa: E402


def test_arbiter_model_is_sonnet_high() -> None:
    assert IMPLEMENTATION_ARBITER_MODEL.name == "claude-sonnet-5-5"
    assert IMPLEMENTATION_ARBITER_MODEL.context.value == "1m"
    assert IMPLEMENTATION_ARBITER_MODEL.reasoning is not None
    assert IMPLEMENTATION_ARBITER_MODEL.reasoning.value == "high"


def test_arbiter_model_resolve_result_id() -> None:
    resolved = ModelResolveResult(model=IMPLEMENTATION_ARBITER_MODEL)
    assert resolved.model.name == "claude-sonnet-5-5"
