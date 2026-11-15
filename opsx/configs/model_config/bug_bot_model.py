"""Пресеты по кругам search-rounds (1 → low, 2 → medium, 3 → high)."""

from __future__ import annotations

from .model_presets import DEFAULT_HOP_MODEL, grok_medium, grok_high

low_model = DEFAULT_HOP_MODEL
medium_model = grok_medium
high_model = grok_high
