"""SDK model selection для ``implementation_arbiter``."""

from __future__ import annotations

from agents.adapters.repair_adapter import (
    default_hop_model_selection,
    implementation_arbiter_model_selection,
)


def test_arbiter_uses_sonnet_high() -> None:
    selection = implementation_arbiter_model_selection()
    assert selection["id"] == "claude-sonnet-5-5"
    params = {p["id"]: p["value"] for p in selection["params"]}
    assert params["context"] == "1m"
    assert params["reasoning_effort"] == "high"


def test_default_hop_still_composer() -> None:
    assert default_hop_model_selection()["id"] == "composer-2.5"
