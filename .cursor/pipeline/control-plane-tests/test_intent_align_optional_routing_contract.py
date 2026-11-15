"""Contract tests for optional /opsx-intent-align discovery on evolution + explore."""

from __future__ import annotations

import pytest

from run_command_transfer_contract_tests import (
    COMMANDS,
    check_intent_align_optional_discovery_routing,
)


@pytest.mark.parametrize(
    ("path", "needle"),
    [
        (
            "opsx-evolution.md",
            "OPTIONAL (chat-only, never mandatory): /opsx-intent-align",
        ),
        (
            "opsx-evolution.md",
            "DEFAULT when OWNER_INTENT is clear: paste COPY_BLOCK",
        ),
        (
            "opsx-evolution.md",
            "Do **not** put `/opsx-intent-align` or any `/opsx-*` inside",
        ),
        (
            "opsx-explore.md",
            "material product-semantic ambiguity",
        ),
        (
            "opsx-explore.md",
            "Don't write intent receipts**",
        ),
        (
            "opsx-explore.md",
            "verbatim",
        ),
    ],
    ids=[
        "evo_optional_route",
        "evo_default_new",
        "evo_no_opsx_in_copy_block",
        "explore_product_ambiguity",
        "explore_no_receipts",
        "explore_verbatim_owner_intent",
    ],
)
def test_intent_align_optional_routing_needles(path: str, needle: str) -> None:
    """Acceptance needles 1–7 are present in command contracts."""

    body = (COMMANDS / path).read_text(encoding="utf-8")
    assert needle in body


def test_intent_align_optional_routing_checker() -> None:
    """Bundled checker matches evolution + explore + fixture (criteria 1–8)."""

    check_intent_align_optional_discovery_routing()
