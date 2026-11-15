"""Регрессия repair-scaffold-slice001-scope: prompt без forbidden paths, legal shape в embed."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from agents.config import ReadScopeViolation
from agents.implementation.apply_scaffold.prompt.get_prompt import get_prompt as scaffold_prompt
from agents.implementation.apply_scaffold.scaffold_hop_visible_text import (
    scaffold_hop_visible_text_is_clean,
)
from agents.implementation.apply_scaffold.scaffold_receipt_write import (
    SCAFFOLD_RECEIPT_SCHEMA_RULE,
    enforce_scaffold_receipt_after_edit,
    scaffold_receipt_write_correction_prompt,
)
from agents.implementation.prompt.slice_input_pack import build_stage_slice_embed
from agents.implementation.tests.test_apply_scaffold_receipt_machine_schema import (
    _VISUAL_HYBRID_RECEIPT,
)

_REPO_ROOT = Path(__file__).resolve().parents[5]
_TEST_CHANGE = "_pytest_scaffold_scope_paths"
_TEST_SLICE = "SLICE-001"
_FORBIDDEN_SNIPPETS = (
    "stage_receipt_writer.py",
    "scaffold_receipt_legal_shape",
    "scaffold_receipt_write",
    "scaffold_hop_visible_text",
    "agent.py",
)


def _assert_scaffold_hop_text_clean(text: str) -> None:
    assert scaffold_hop_visible_text_is_clean(text), "forbidden .py / opsx/ / openspec/schemas"
    lowered = text.lower()
    for snippet in _FORBIDDEN_SNIPPETS:
        assert snippet.lower() not in lowered, snippet


def _scaffold_dir() -> Path:
    return (
        _REPO_ROOT
        / "openspec"
        / "changes"
        / _TEST_CHANGE
        / "apply-scaffold"
        / _TEST_SLICE
    )


def _scaffold_relpath() -> str:
    return (
        f"openspec/changes/{_TEST_CHANGE}/apply-scaffold/{_TEST_SLICE}/SCAFFOLD.json"
    )


@pytest.fixture
def scaffold_receipt_path() -> Path:
    folder = _scaffold_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "SCAFFOLD.json"
    yield path
    if folder.parent.parent.exists():
        shutil.rmtree(folder.parent.parent, ignore_errors=True)


@pytest.mark.unit
def test_scaffold_prompt_has_no_forbidden_runtime_paths(tmp_path: Path) -> None:
    prompt = scaffold_prompt(
        repository_path=str(tmp_path),
        change_id="nope",
        impl_verify_answer={"slice_id": "SLICE-001"},
        artifact_bundle={},
        machine_context={},
    )
    _assert_scaffold_hop_text_clean(prompt)
    assert "ready_branch_required" in prompt
    assert "blocked_branch_required" in prompt
    assert "BLOCKED branch" in prompt


@pytest.mark.unit
def test_scaffold_receipt_correction_has_field_lists_without_forbidden_paths() -> None:
    violation = ReadScopeViolation(
        tool="edit",
        path="openspec/changes/visual/apply-scaffold/SLICE-001/SCAFFOLD.json",
        rule="scaffold_receipt_schema",
        command="oneOf_mismatch: production_paths: non-no-op SCAFFOLD_READY requires created or extended paths",
    )
    text = scaffold_receipt_write_correction_prompt(violation, base_prompt="# hop")
    _assert_scaffold_hop_text_clean(text)
    assert "BLOCKED branch" in text
    assert "SCAFFOLD_READY branch" in text
    assert 'status (const "BLOCKED")' in text
    assert "work_scope_digest" in text
    assert "Path:" not in text


@pytest.mark.unit
def test_scaffold_input_pack_embeds_legal_receipt_shape(tmp_path: Path) -> None:
    embed = build_stage_slice_embed(
        stage_key="apply_scaffold",
        change_id="visual",
        slice_id="SLICE-001",
        repo_root=tmp_path,
    )
    shape = embed.get("scaffold_json_field_lists")
    assert isinstance(shape, dict)
    blocked = shape.get("blocked_branch_required")
    ready = shape.get("ready_branch_required")
    assert isinstance(blocked, list) and len(blocked) >= 8
    assert isinstance(ready, list) and len(ready) >= 12
    assert "work_scope_digest" in shape.get("runtime_owned_fields", [])
    serialized = json.dumps(embed)
    _assert_scaffold_hop_text_clean(serialized)


@pytest.mark.unit
def test_oneof_rejection_correction_from_live_edit_guard(
    scaffold_receipt_path: Path,
) -> None:
    """oneOf: correction text из реального edit guard без path leaks."""
    prior = json.dumps({"status": "prior"}, indent=2)
    _ = scaffold_receipt_path.write_text(prior, encoding="utf-8")
    snapshots: dict[str, str] = {}
    args = {"path": _scaffold_relpath()}
    started = {"kind": "interaction", "update_type": "tool-call-started"}
    enforce_scaffold_receipt_after_edit(
        "edit",
        args,
        started,
        repo_root=_REPO_ROOT,
        snapshots=snapshots,
    )
    _ = scaffold_receipt_path.write_text(
        json.dumps(_VISUAL_HYBRID_RECEIPT),
        encoding="utf-8",
    )
    completed = {"kind": "interaction", "update_type": "tool-call-completed"}
    with pytest.raises(ReadScopeViolation) as err:
        enforce_scaffold_receipt_after_edit(
            "edit",
            args,
            completed,
            repo_root=_REPO_ROOT,
            snapshots=snapshots,
        )
    assert err.value.rule == SCAFFOLD_RECEIPT_SCHEMA_RULE
    text = scaffold_receipt_write_correction_prompt(
        err.value,
        base_prompt="# hop",
    )
    _assert_scaffold_hop_text_clean(text)
    assert "BLOCKED branch" in text
    assert "SCAFFOLD_READY branch" in text
    combined = (err.value.command or "").lower()
    assert "oneof" in combined or "oneof_mismatch" in combined
