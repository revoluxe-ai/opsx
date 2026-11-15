"""Тесты монтирования contract_prompts: группы, idempotency, fail-loud."""

from __future__ import annotations

import pytest

from agents.common.contract_prompts import (
    ContractPromptMissingError,
    fragment_marker,
    group_names,
    load_manifest,
    mount,
    prepend_contracts,
)


def test_every_manifest_group_mounts_expected_markers() -> None:
    """Каждая группа из manifest даёт маркер для каждого фрагмента."""
    manifest = load_manifest()
    for group, names in manifest.items():
        text = mount(group)
        for name in names:
            assert fragment_marker(name) in text, f"group {group} missing {name}"


def test_missing_fragment_raises() -> None:
    with pytest.raises(ContractPromptMissingError):
        mount("implementation", extra=("nonexistent-fragment-xyz",))


def test_second_prepend_does_not_duplicate() -> None:
    base = "body\n"
    once = prepend_contracts(base, group="common")
    twice = prepend_contracts(once, group="common")
    assert once == twice
    for name in group_names("common"):
        assert twice.count(fragment_marker(name)) == 1
