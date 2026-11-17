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


def test_hop_contracts_do_not_cite_trees_outside_slice_allow() -> None:
    """Non-discovery slice hop (read/edit) cancels on the second outside_restrict_allow.

    Contract text mounted for every implementation, repair, and verify hop must
    not name a runtime or schema tree the slice restrict list excludes.
    """
    forbidden = ("opsx/", ".opsx/", ".cursor/", "openspec/schemas/")
    for group in ("common", "planning", "implementation", "verify", "repair"):
        text = mount(group)
        for prefix in forbidden:
            assert prefix not in text, f"{group} cites {prefix}"


def test_second_prepend_does_not_duplicate() -> None:
    base = "body\n"
    once = prepend_contracts(base, group="common")
    twice = prepend_contracts(once, group="common")
    assert once == twice
    for name in group_names("common"):
        assert twice.count(fragment_marker(name)) == 1
