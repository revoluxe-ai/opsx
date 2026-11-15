"""Explicit legacy tier selector -> Cursor SDK ModelRoute translation boundary.

Purpose: terminate every legacy/Task-style model alias from
``tools/temporal/agent-models.json`` at one explicit mapping before it can
reach the Cursor Python SDK. Temporal lanes (``sdk_cursor_adapter.py``) and
audit tooling (``active_model_route_coverage.py``) resolve through
:func:`resolve_sdk_model_route` only.

Live-catalog evidence (fetched 2026-09-17, 39 models):
- ``gpt-5.6-luna`` params: ``context`` {272k, 1m}, ``reasoning``
  {none, low, medium, high, xhigh, max}, ``fast`` {false, true}.
- ``grok-4.6`` params: ``effort`` {low, medium, high, xhigh}, ``fast``
  {false, true}. NOTE: Grok uses ``effort``, NOT ``reasoning``; it has no
  ``context`` param. Cross-family parameter names must never be guessed.
- ``composer-2.5`` params: ``fast`` {false, true}.
- ``gpt-5.6-sol`` params: same shape as Luna.

Contract:
- Legacy selectors (``gpt-5.6-luna-xhigh``, ``cursor-grok-4.6-medium`` and
  their ``-fast`` speed variants) are NEVER passed as SDK ``model_id``.
- Unknown selectors and bare base ids without explicit params fail closed
  with ``ValueError``. No fuzzy translation, no fallback model, no silent
  defaults for unknown input.
- ``model_config=None`` keeps the historical explicit bootstrap default
  (``gpt-5.6-sol`` exact route). Canonical lane configuration always passes
  a real config; ``None`` is test/bootstrap-only, not a fallback selector.
"""

from __future__ import annotations

from cursor_executor.schemas import ModelRoute
from model_config import AgentModelConfig

_SOL_EXACT = ModelRoute(
    "gpt-5.6-sol",
    {"context": "272k", "reasoning": "high", "fast": "false"},
)
_COMPOSER_EXACT = ModelRoute("composer-2.5", {})

# Exact SDK routes for selectors that are already valid SDK model ids.
_EXACT_SDK_ROUTES: dict[str, ModelRoute] = {
    "gpt-5.6-sol": _SOL_EXACT,
    "composer-2.5": _COMPOSER_EXACT,
}

# Explicit legacy alias -> exact SDK route translation boundary.
# Luna xhigh: suffix encodes reasoning=xhigh; speed=standard encodes
# fast=false; context=272k matches the established gpt-5.6-sol precedent
# (272k/xhigh/false is a real live-catalog variant).
# Grok medium: suffix encodes effort=medium (Grok's own param id);
# speed=standard encodes fast=false.
_LEGACY_SELECTOR_TO_SDK_ROUTE: dict[str, ModelRoute] = {
    "gpt-5.6-luna-xhigh": ModelRoute(
        "gpt-5.6-luna",
        {"context": "272k", "reasoning": "xhigh", "fast": "false"},
    ),
    "gpt-5.6-luna-xhigh-fast": ModelRoute(
        "gpt-5.6-luna",
        {"context": "272k", "reasoning": "xhigh", "fast": "true"},
    ),
    "cursor-grok-4.6-medium": ModelRoute(
        "grok-4.6",
        {"effort": "medium", "fast": "false"},
    ),
    "cursor-grok-4.6-medium-fast": ModelRoute(
        "grok-4.6",
        {"effort": "medium", "fast": "true"},
    ),
    # Base ids are already exact; listed here so the boundary is total
    # over every selector the canonical config may carry.
    "composer-2.5": _COMPOSER_EXACT,
    "gpt-5.6-sol": _SOL_EXACT,
}


def resolve_legacy_selector(selector: str) -> ModelRoute:
    """Resolve one configured model selector to its exact SDK route.

    Raises ``ValueError`` for unknown selectors (fail closed, no fallback).
    """
    name = (selector or "").strip()
    if name in _EXACT_SDK_ROUTES:
        return _EXACT_SDK_ROUTES[name]
    if name in _LEGACY_SELECTOR_TO_SDK_ROUTE:
        return _LEGACY_SELECTOR_TO_SDK_ROUTE[name]
    raise ValueError(
        f"Unknown model selector {selector!r}: no exact SDK route; "
        "failing closed (no fallback model, no parameter guessing)."
    )


def resolve_sdk_model_route(
    model_config: AgentModelConfig | None,
    *,
    step: str | None = None,
) -> ModelRoute:
    """Resolve the tier for ``step`` to its exact SDK ModelRoute.

    Legacy aliases terminate at :data:`_LEGACY_SELECTOR_TO_SDK_ROUTE`;
    unknown selectors raise ``ValueError`` instead of passing through.
    """
    if model_config is None:
        return _EXACT_SDK_ROUTES["gpt-5.6-sol"]
    tier = model_config.resolve_step(step or "review")
    return resolve_legacy_selector(tier.cli_model)


def write_policy_for_step(step: str | None) -> str:
    if step in {"repair", "verification_executor", "commit-all"}:
        return "MUTATING"
    return "READ_ONLY"
