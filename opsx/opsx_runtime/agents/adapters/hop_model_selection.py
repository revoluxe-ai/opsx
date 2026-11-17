"""Neutral hop model selection for GraphState and adapter transport."""

from __future__ import annotations

from opsx.project_context import resource_path, project_root

import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = project_root()
_CONFIGS_ROOT = resource_path(_REPO_ROOT, "opsx", "configs")
if str(_CONFIGS_ROOT) not in sys.path:
    sys.path.insert(0, str(_CONFIGS_ROOT))

from model_config.models.base_model_setting import BaseModelSetting  # noqa: E402
from model_config.models.claude_haiku import ClaudeHaiku  # noqa: E402
from model_config.models.claude_sonnet import ClaudeSonnet55  # noqa: E402
from model_config.models.composer import Composer  # noqa: E402
from model_config.models.grok_46 import Grok46  # noqa: E402
from model_config.models.grok_47 import Grok47  # noqa: E402
from model_config.resolve_model import ModelResolveResult  # noqa: E402


def _hop_model_params(model: BaseModelSetting) -> list[dict[str, str]]:
    """Map resolved model profile to adapter ``params`` payload."""
    if isinstance(model, ClaudeHaiku):
        params: list[dict[str, str]] = [
            {"id": "context", "value": model.context.value},
        ]
        if model.reasoning is not None:
            params.append(
                {"id": "reasoning_effort", "value": model.reasoning.value},
            )
        return params
    if isinstance(model, ClaudeSonnet55):
        params = [{"id": "context", "value": model.context.value}]
        if model.reasoning is not None:
            params.append(
                {"id": "reasoning_effort", "value": model.reasoning.value},
            )
        return params
    if isinstance(model, Composer):
        fast_value = "true" if model.fast else "false"
        return [{"id": "fast", "value": fast_value}]
    if isinstance(model, Grok46):
        fast_value = "true" if model.fast else "false"
        return [
            {"id": "effort", "value": model.reasoning_effort.value},
            {"id": "fast", "value": fast_value},
        ]
    if isinstance(model, Grok47):
        fast_value = "true" if model.fast else "false"
        return [
            {"id": "context", "value": model.context.value},
            {"id": "reasoning_effort", "value": model.reasoning_effort.value},
            {"id": "fast", "value": fast_value},
        ]
    raise TypeError(f"unsupported resolved model type: {type(model)!r}")


def model_selection_for_hop(resolved: ModelResolveResult) -> dict[str, Any]:
    """Build ``state['model']`` selection dict for adapter hops."""
    model = resolved.model
    return {"id": model.name, "params": _hop_model_params(model)}


def default_hop_model_selection() -> dict[str, Any]:
    """Default hop model for GraphState (``DEFAULT_HOP_MODEL``)."""
    from model_config.model_presets import DEFAULT_HOP_MODEL

    return model_selection_for_hop(ModelResolveResult(model=DEFAULT_HOP_MODEL))


def supervisor_fix_model_selection() -> dict[str, Any]:
    """Пресет супервизора: Grok 4.7 Medium Standard (``SUPERVISOR_FIX_MODEL``)."""
    from model_config.model_presets import SUPERVISOR_FIX_MODEL

    return model_selection_for_hop(ModelResolveResult(model=SUPERVISOR_FIX_MODEL))


def implementation_arbiter_model_selection() -> dict[str, Any]:
    """Model selection for ``implementation_arbiter`` resume hops."""
    from model_config.model_presets import IMPLEMENTATION_ARBITER_MODEL

    return model_selection_for_hop(ModelResolveResult(model=IMPLEMENTATION_ARBITER_MODEL))
