"""IMPLEMENT receipt freeze must echo an accepted RED test_content_identity.

RED materialize stores the legacy concat digest. The implement writer used to
store the canonical live digest, so unchanged frozen bytes failed
``frozen_test_content_identity must equal red_identity.test_content_identity``.
A declared digest that no longer matches on-disk bytes must still be rejected.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from workflows.implementation.hop_worktree import capture_hop_worktree_baseline
from workflows.implementation.stage_receipt_writer import StageReceiptWriter


def _init_repo(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    _ = (tmp_path / "opsx" / "project").mkdir(parents=True, exist_ok=True)
    _ = (tmp_path / "marker.txt").write_text("seed", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "seed"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )


def _legacy_concat_digest(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _write_slice_inputs(
    tmp_path: Path,
    change_id: str,
    slice_id: str,
    *,
    test_rel: str,
    test_digest: str,
) -> None:
    work = "src/pkg/widget.py"
    design_dir = tmp_path / "openspec" / "changes" / change_id / "apply-design"
    design_dir.mkdir(parents=True, exist_ok=True)
    design = {
        "schema": "opsx.apply_design",
        "schema_version": 3,
        "change_id": change_id,
        "status": "APPLY_DESIGN_READY",
        "planning_identity": {"planning_digest": "a" * 64},
        "repository_identity": {"inspected_digest": "b" * 64},
        "protected_context_identity": {"content_digest": "c" * 64},
        "slices": [
            {
                "slice_id": slice_id,
                "expected_product_write_surfaces": [work],
                "scaffold_contract": {
                    "no_op_allowed": True,
                    "required_surfaces": [
                        {
                            "path": work,
                            "action": "EXTEND",
                            "required_symbols": ["widget"],
                        }
                    ],
                    "allowed_scaffold_writes": [work],
                },
            }
        ],
    }
    _ = (design_dir / "APPLY-DESIGN.json").write_text(
        json.dumps(design, indent=2),
        encoding="utf-8",
    )
    scaffold_dir = tmp_path / "openspec" / "changes" / change_id / "apply-scaffold" / slice_id
    red_dir = tmp_path / "openspec" / "changes" / change_id / "apply-red" / slice_id
    scaffold_dir.mkdir(parents=True, exist_ok=True)
    red_dir.mkdir(parents=True, exist_ok=True)
    _ = (scaffold_dir / "SCAFFOLD.json").write_text(
        json.dumps(
            {
                "schema": "opsx.apply_scaffold",
                "schema_version": 1,
                "change_id": change_id,
                "slice_id": slice_id,
                "status": "SCAFFOLD_READY",
                "chat_history_required": False,
                "next_stage": "APPLY_RED",
            },
        ),
        encoding="utf-8",
    )
    _ = (red_dir / "RED.json").write_text(
        json.dumps(
            {
                "schema": "opsx.apply_red",
                "schema_version": 2,
                "change_id": change_id,
                "slice_id": slice_id,
                "status": "RED_PROVEN",
                "chat_history_required": False,
                "tests_frozen": True,
                "test_paths": [test_rel],
                "fixture_paths": [],
                "test_content_identity": test_digest,
                "next_stage": "APPLY_IMPLEMENT",
            },
        ),
        encoding="utf-8",
    )


def _prepare(tmp_path: Path) -> tuple[str, str, str, Path]:
    change_id = "hop-decision"
    slice_id = "SLICE-001"
    test_rel = "tests/frozen/test_hold.py"
    work = "src/pkg/widget.py"
    _init_repo(tmp_path)
    product = tmp_path / work
    product.parent.mkdir(parents=True, exist_ok=True)
    _ = product.write_text("def widget():\n    pass\n", encoding="utf-8")
    test_path = tmp_path / test_rel
    test_path.parent.mkdir(parents=True, exist_ok=True)
    _ = test_path.write_text("def test_hold():\n    assert False\n", encoding="utf-8")
    digest = _legacy_concat_digest(test_path)
    _write_slice_inputs(
        tmp_path,
        change_id,
        slice_id,
        test_rel=test_rel,
        test_digest=digest,
    )
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "slice"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    baseline = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    _ = product.write_text("def widget():\n    return 1\n", encoding="utf-8")
    return change_id, slice_id, baseline, test_path


@pytest.mark.unit
def test_written_receipt_uses_declared_red_test_content_identity(tmp_path: Path) -> None:
    """Unchanged frozen bytes: IMPLEMENT freeze equals RED legacy digest."""
    change_id, slice_id, baseline, test_path = _prepare(tmp_path)
    declared = _legacy_concat_digest(test_path)
    result = StageReceiptWriter.record_hop_decision(
        change_id,
        repo_root=tmp_path,
        decision={
            "stage": "APPLY_IMPLEMENT",
            "slice_id": slice_id,
            "hop_status": "APPLY_COMPLETE",
            "hop_worktree_baseline": baseline,
            "hop_files_changed": ["src/pkg/widget.py"],
        },
    )
    assert result.ok, result.code
    assert result.path is not None
    body = json.loads(result.path.read_text(encoding="utf-8"))
    assert body["status"] == "IMPLEMENTATION_WRITTEN"
    assert body["frozen_test_content_identity"] == declared
    assert body["red_identity"]["test_content_identity"] == declared
    assert body["frozen_test_identity_before"] == declared
    assert body["frozen_test_identity_after"] == declared


@pytest.mark.unit
def test_off_head_product_surface_materializes_without_hop_edit(tmp_path: Path) -> None:
    """Продукт вне HEAD, не тронутый этим hop, всё равно сдаёт IMPLEMENTATION_WRITTEN.

    Снимок уже содержит файл (scaffold или прошлый hop). Пустой delta этого hop
    не должен давать IMPLEMENT_NO_PRODUCT_DELTA. Файл, совпадающий с HEAD, по-прежнему отказ.
    """
    change_id = "hop-decision"
    slice_id = "SLICE-001"
    test_rel = "tests/frozen/test_hold.py"
    work = "src/pkg/widget.py"
    _init_repo(tmp_path)
    test_path = tmp_path / test_rel
    test_path.parent.mkdir(parents=True, exist_ok=True)
    _ = test_path.write_text("def test_hold():\n    assert False\n", encoding="utf-8")
    digest = _legacy_concat_digest(test_path)
    _write_slice_inputs(
        tmp_path,
        change_id,
        slice_id,
        test_rel=test_rel,
        test_digest=digest,
    )
    product = tmp_path / work
    product.parent.mkdir(parents=True, exist_ok=True)
    _ = product.write_text("def widget():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "slice"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    committed = capture_hop_worktree_baseline(tmp_path, [work])
    clean = StageReceiptWriter.record_hop_decision(
        change_id,
        repo_root=tmp_path,
        decision={
            "stage": "APPLY_IMPLEMENT",
            "slice_id": slice_id,
            "hop_status": "APPLY_COMPLETE",
            "hop_worktree_baseline": committed,
            "hop_files_changed": [],
        },
    )
    assert clean.ok is False
    assert clean.code == "IMPLEMENT_NO_PRODUCT_DELTA"

    _ = product.write_text("def widget():\n    return 2\n", encoding="utf-8")
    baseline = capture_hop_worktree_baseline(tmp_path, [work])
    result = StageReceiptWriter.record_hop_decision(
        change_id,
        repo_root=tmp_path,
        decision={
            "stage": "APPLY_IMPLEMENT",
            "slice_id": slice_id,
            "hop_status": "APPLY_COMPLETE",
            "hop_worktree_baseline": baseline,
            "hop_files_changed": [],
        },
    )
    assert result.ok, result.code
    assert result.path is not None
    body = json.loads(result.path.read_text(encoding="utf-8"))
    assert body["status"] == "IMPLEMENTATION_WRITTEN"
    assert body["production_paths_created"] == []
    assert work in body["production_paths_modified"]


@pytest.mark.unit
def test_mutated_frozen_bytes_still_fail_implement_validate(tmp_path: Path) -> None:
    """Declared digest that no longer matches file bytes is not accepted."""
    change_id, slice_id, baseline, test_path = _prepare(tmp_path)
    _ = test_path.write_text("def test_hold():\n    assert True\n", encoding="utf-8")
    result = StageReceiptWriter.record_hop_decision(
        change_id,
        repo_root=tmp_path,
        decision={
            "stage": "APPLY_IMPLEMENT",
            "slice_id": slice_id,
            "hop_status": "APPLY_COMPLETE",
            "hop_worktree_baseline": baseline,
            "hop_files_changed": ["src/pkg/widget.py"],
        },
    )
    assert result.ok is False
    assert result.code is not None
    assert "IMPLEMENT_VALIDATE_FAILED" in result.code


def _node_json(script: str, cwd: Path) -> dict:
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.mark.unit
def test_canonical_freeze_matches_legacy_red_identity_when_bytes_unchanged(tmp_path: Path) -> None:
    """Same frozen bytes may be legacy concat on RED and canonical on IMPLEMENT.

    Those two digests differ. The implement gate accepts both fields when each
    is an accepted digest of the current files. Mutated bytes stay rejected.
    """
    core = Path(__file__).resolve().parents[5]
    test_a = tmp_path / "tests" / "frozen" / "test_a.py"
    test_b = tmp_path / "tests" / "frozen" / "test_b.py"
    test_a.parent.mkdir(parents=True, exist_ok=True)
    _ = test_a.write_text("def test_a():\n    assert False\n", encoding="utf-8")
    _ = test_b.write_text("def test_b():\n    assert False\n", encoding="utf-8")
    paths = [
        "tests/frozen/test_a.py",
        "tests/frozen/test_b.py",
    ]
    digests = _node_json(
        f"""
        const m = await import('./opsx_tools/opsx-apply-pipeline-identity.mjs');
        const red = {{
          test_paths: {json.dumps(paths)},
          fixture_paths: [],
        }};
        const legacy = m.legacyFrozenTestContentIdentityFromRed(red, {json.dumps(str(tmp_path))});
        const canonical = m.canonicalFrozenTestContentIdentityFromRed(red, {json.dumps(str(tmp_path))});
        console.log(JSON.stringify({{
          legacy: legacy.digest,
          canonical: canonical.identity,
        }}));
        """,
        core,
    )
    legacy = str(digests["legacy"])
    canonical = str(digests["canonical"])
    assert legacy != canonical
    red_rel = "openspec/changes/hop-decision/apply-red/SLICE-001/RED.json"
    red_path = tmp_path / red_rel
    red_path.parent.mkdir(parents=True, exist_ok=True)
    red_body = {
        "status": "RED_PROVEN",
        "tests_frozen": True,
        "test_paths": paths,
        "fixture_paths": [],
        "test_content_identity": legacy,
    }
    _ = red_path.write_text(json.dumps(red_body), encoding="utf-8")
    artifact = {
        "schema": "opsx.apply_implement",
        "schema_version": 1,
        "change_id": "hop-decision",
        "slice_id": "SLICE-001",
        "status": "IMPLEMENTATION_WRITTEN",
        "chat_history_required": False,
        "tests_executed": False,
        "tests_modified": False,
        "next_stage": "APPLY_GREEN",
        "scaffold_identity": {"status": "SCAFFOLD_READY"},
        "production_paths_modified": ["src/pkg/widget.py"],
        "production_paths_created": [],
        "red_identity": {
            "machine_path": red_rel,
            "status": "RED_PROVEN",
            "test_content_identity": legacy,
        },
        "frozen_test_content_identity": canonical,
        "frozen_test_identity_before": canonical,
        "frozen_test_identity_after": canonical,
    }
    verdict = _node_json(
        f"""
        const impl = await import('./opsx_tools/opsx-apply-implement.mjs');
        const artifact = {json.dumps(artifact)};
        const verdict = impl.validateApplyImplementArtifact({{
          artifact,
          schema: {{ type: "object", additionalProperties: true }},
          design: null,
          repoRoot: {json.dumps(str(tmp_path))},
        }});
        console.log(JSON.stringify(verdict));
        """,
        core,
    )
    messages = [str(item.get("message") or "") for item in verdict.get("errors") or []]
    assert not any("must equal red_identity.test_content_identity" in msg for msg in messages)
    assert not any("authoritative RED frozen bytes" in msg for msg in messages)
    _ = test_a.write_text("def test_a():\n    assert True\n", encoding="utf-8")
    mutated = _node_json(
        f"""
        const impl = await import('./opsx_tools/opsx-apply-implement.mjs');
        const artifact = {json.dumps(artifact)};
        const verdict = impl.validateApplyImplementArtifact({{
          artifact,
          schema: {{ type: "object", additionalProperties: true }},
          design: null,
          repoRoot: {json.dumps(str(tmp_path))},
        }});
        console.log(JSON.stringify(verdict));
        """,
        core,
    )
    mutated_messages = [str(item.get("message") or "") for item in mutated.get("errors") or []]
    assert any(
        "must equal red_identity.test_content_identity" in msg
        or "authoritative RED frozen bytes" in msg
        for msg in mutated_messages
    )


def _stamp_red_identities(
    tmp_path: Path,
    change_id: str,
    slice_id: str,
    *,
    top: str,
    operative: str,
) -> None:
    red_path = (
        tmp_path
        / "openspec"
        / "changes"
        / change_id
        / "apply-red"
        / slice_id
        / "RED.json"
    )
    red = json.loads(red_path.read_text(encoding="utf-8"))
    red["test_content_identity"] = top
    red["operative_red_transition"] = {
        "verifier_id": "opsx-apply-red-transition-gate",
        "permitted_transition": "RED_PROVEN",
        "test_content_identity": operative,
    }
    _ = red_path.write_text(json.dumps(red), encoding="utf-8")


def _canonical_digest(core: Path, repo: Path, test_rel: str) -> str:
    payload = _node_json(
        f"""
        const m = await import('./opsx_tools/opsx-apply-pipeline-identity.mjs');
        const red = {{
          test_paths: [{json.dumps(test_rel)}],
          fixture_paths: [],
        }};
        const canonical = m.canonicalFrozenTestContentIdentityFromRed(
          red,
          {json.dumps(str(repo))},
        );
        console.log(JSON.stringify({{ canonical: canonical.identity }}));
        """,
        core,
    )
    return str(payload["canonical"])


@pytest.mark.unit
def test_stale_top_level_uses_operative_transition_digest(tmp_path: Path) -> None:
    """Top-level RED digest can be stale while the transition digest matches bytes.

    IMPLEMENT must stamp that accepted transition digest into both
    frozen_test_content_identity and red_identity.test_content_identity.
    """
    change_id, slice_id, baseline, _test_path = _prepare(tmp_path)
    core = Path(__file__).resolve().parents[5]
    test_rel = "tests/frozen/test_hold.py"
    canonical = _canonical_digest(core, tmp_path, test_rel)
    _stamp_red_identities(
        tmp_path,
        change_id,
        slice_id,
        top="ab" * 32,
        operative=canonical,
    )
    result = StageReceiptWriter.record_hop_decision(
        change_id,
        repo_root=tmp_path,
        decision={
            "stage": "APPLY_IMPLEMENT",
            "slice_id": slice_id,
            "hop_status": "APPLY_COMPLETE",
            "hop_worktree_baseline": baseline,
            "hop_files_changed": ["src/pkg/widget.py"],
        },
    )
    assert result.ok, result.code
    assert result.path is not None
    body = json.loads(result.path.read_text(encoding="utf-8"))
    assert body["frozen_test_content_identity"] == canonical
    assert body["red_identity"]["test_content_identity"] == canonical
    assert body["frozen_test_identity_before"] == canonical
    assert body["frozen_test_identity_after"] == canonical


@pytest.mark.unit
def test_gate_accepts_legacy_and_canonical_when_top_level_is_stale(tmp_path: Path) -> None:
    """Two accepted on-disk digests pass even if the RED top-level digest matches neither.

    The transition stamp may be the canonical digest while one receipt field
    still holds the legacy concat. A third stale top-level value must not make
    that pair fail equality. A digest of mutated bytes still fails.
    """
    core = Path(__file__).resolve().parents[5]
    test_a = tmp_path / "tests" / "frozen" / "test_a.py"
    test_b = tmp_path / "tests" / "frozen" / "test_b.py"
    test_a.parent.mkdir(parents=True, exist_ok=True)
    _ = test_a.write_text("def test_a():\n    assert False\n", encoding="utf-8")
    _ = test_b.write_text("def test_b():\n    assert False\n", encoding="utf-8")
    paths = ["tests/frozen/test_a.py", "tests/frozen/test_b.py"]
    digests = _node_json(
        f"""
        const m = await import('./opsx_tools/opsx-apply-pipeline-identity.mjs');
        const red = {{
          test_paths: {json.dumps(paths)},
          fixture_paths: [],
        }};
        const legacy = m.legacyFrozenTestContentIdentityFromRed(red, {json.dumps(str(tmp_path))});
        const canonical = m.canonicalFrozenTestContentIdentityFromRed(red, {json.dumps(str(tmp_path))});
        console.log(JSON.stringify({{
          legacy: legacy.digest,
          canonical: canonical.identity,
        }}));
        """,
        core,
    )
    legacy = str(digests["legacy"])
    canonical = str(digests["canonical"])
    assert legacy != canonical
    red_rel = "openspec/changes/hop-decision/apply-red/SLICE-001/RED.json"
    red_path = tmp_path / red_rel
    red_path.parent.mkdir(parents=True, exist_ok=True)
    red_body = {
        "status": "RED_PROVEN",
        "tests_frozen": True,
        "test_paths": paths,
        "fixture_paths": [],
        "test_content_identity": "ab" * 32,
        "operative_red_transition": {
            "verifier_id": "opsx-apply-red-transition-gate",
            "permitted_transition": "RED_PROVEN",
            "test_content_identity": canonical,
        },
    }
    _ = red_path.write_text(json.dumps(red_body), encoding="utf-8")
    artifact = {
        "schema": "opsx.apply_implement",
        "schema_version": 1,
        "change_id": "hop-decision",
        "slice_id": "SLICE-001",
        "status": "IMPLEMENTATION_WRITTEN",
        "chat_history_required": False,
        "tests_executed": False,
        "tests_modified": False,
        "next_stage": "APPLY_GREEN",
        "scaffold_identity": {"status": "SCAFFOLD_READY"},
        "production_paths_modified": ["src/pkg/widget.py"],
        "production_paths_created": [],
        "red_identity": {
            "machine_path": red_rel,
            "status": "RED_PROVEN",
            "test_content_identity": legacy,
        },
        "frozen_test_content_identity": canonical,
        "frozen_test_identity_before": canonical,
        "frozen_test_identity_after": canonical,
    }
    verdict = _node_json(
        f"""
        const impl = await import('./opsx_tools/opsx-apply-implement.mjs');
        const artifact = {json.dumps(artifact)};
        const verdict = impl.validateApplyImplementArtifact({{
          artifact,
          schema: {{ type: "object", additionalProperties: true }},
          design: null,
          repoRoot: {json.dumps(str(tmp_path))},
        }});
        console.log(JSON.stringify(verdict));
        """,
        core,
    )
    messages = [str(item.get("message") or "") for item in verdict.get("errors") or []]
    assert not any("must equal red_identity.test_content_identity" in msg for msg in messages)
    assert not any("authoritative RED frozen bytes" in msg for msg in messages)


@pytest.mark.unit
def test_writer_prefers_accepted_operative_digest_for_both_fields(tmp_path: Path) -> None:
    """Top-level legacy and transition canonical are both on-disk variants.

    IMPLEMENT stamps the transition digest into frozen_test_content_identity
    and red_identity.test_content_identity.
    """
    change_id, slice_id, baseline, test_path = _prepare(tmp_path)
    core = Path(__file__).resolve().parents[5]
    test_rel = "tests/frozen/test_hold.py"
    legacy = _legacy_concat_digest(test_path)
    canonical = _canonical_digest(core, tmp_path, test_rel)
    assert legacy != canonical
    _stamp_red_identities(
        tmp_path,
        change_id,
        slice_id,
        top=legacy,
        operative=canonical,
    )
    result = StageReceiptWriter.record_hop_decision(
        change_id,
        repo_root=tmp_path,
        decision={
            "stage": "APPLY_IMPLEMENT",
            "slice_id": slice_id,
            "hop_status": "APPLY_COMPLETE",
            "hop_worktree_baseline": baseline,
            "hop_files_changed": ["src/pkg/widget.py"],
        },
    )
    assert result.ok, result.code
    assert result.path is not None
    body = json.loads(result.path.read_text(encoding="utf-8"))
    assert body["frozen_test_content_identity"] == canonical
    assert body["red_identity"]["test_content_identity"] == canonical
    assert body["frozen_test_identity_before"] == canonical
    assert body["frozen_test_identity_after"] == canonical


@pytest.mark.unit
def test_unmatched_operative_digest_still_fails_implement_validate(tmp_path: Path) -> None:
    """A transition digest that is not an on-disk variant does not pass the gate."""
    change_id, slice_id, baseline, _test_path = _prepare(tmp_path)
    _stamp_red_identities(
        tmp_path,
        change_id,
        slice_id,
        top="ab" * 32,
        operative="cd" * 32,
    )
    result = StageReceiptWriter.record_hop_decision(
        change_id,
        repo_root=tmp_path,
        decision={
            "stage": "APPLY_IMPLEMENT",
            "slice_id": slice_id,
            "hop_status": "APPLY_COMPLETE",
            "hop_worktree_baseline": baseline,
            "hop_files_changed": ["src/pkg/widget.py"],
        },
    )
    assert result.ok is False
    assert result.code is not None
    assert "IMPLEMENT_VALIDATE_FAILED" in result.code
