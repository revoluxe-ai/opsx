"""Готовые объекты профилей моделей для resolve и рантайма."""

from __future__ import annotations

from .models.claude_haiku import ClaudeHaiku, Context as HaikuContext, Reasoning as HaikuReasoning
from .models.claude_sonnet import ClaudeSonnet55, Context as SonnetContext, Reasoning as SonnetReasoning
from .models.composer import Composer
from .models.grok_46 import Grok46Low, ReasoningEffort as Grok46ReasoningEffort
from .models.grok_47 import Context as GrokContext
from .models.grok_47 import Grok47, Grok47Low, ReasoningEffort

composer_standart = Composer()

grok_medium = Grok47(
    context=GrokContext.CTX_500K,
    reasoning_effort=ReasoningEffort.MEDIUM,
    fast=False,
)
grok_high = Grok47(
    context=GrokContext.CTX_256K,
    reasoning_effort=ReasoningEffort.HIGH,
    fast=False,
)
grok_low = Grok47Low(
    context=GrokContext.CTX_256K,
    reasoning_effort=ReasoningEffort.LOW,
    fast=False,
)

grok_46_low = Grok46Low(
    reasoning_effort=Grok46ReasoningEffort.LOW,
    fast=False,
)

# Опциональный профиль Haiku (smoke/alias); не дефолт hop-ов.
haiku_low_model = ClaudeHaiku(
    context=HaikuContext.CTX_300K,
    reasoning=HaikuReasoning.LOW,
    fast=False,
)

# ЕДИНАЯ НАСТРОЙКА дефолтной модели: Composer 2.5 Standard для всех hop-ов verify/repair/resolve.
# Grok — только search-rounds 2–3 (medium/high).
DEFAULT_HOP_MODEL = composer_standart
super_low_model = DEFAULT_HOP_MODEL

# Implementation arbiter: отдельный high-профиль (Owner: claude-sonnet-5-5 high).
arbiter_high_model = ClaudeSonnet55(
    context=SonnetContext.CTX_1M,
    reasoning=SonnetReasoning.HIGH,
)
IMPLEMENTATION_ARBITER_MODEL = arbiter_high_model

# CLI / --model-id alias (subagent slug); SDK id остаётся ``grok-4.7`` + params.
GROK_47_LOW_PRESET_SLUG = "grok-4.7-low"
# CLI alias ``grok-4.6-low`` → SDK id ``grok-4.6`` + ``effort=low``, ``fast=false``.
GROK_46_LOW_PRESET_SLUG = "grok-4.6-low"
