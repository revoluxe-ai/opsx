"""``$from_path`` и recompute для submit_apply_design."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from agents.implementation.apply_design.submit_content_resolver import (
    resolve_submit_apply_design_content,
)


@pytest.mark.asyncio
async def test_from_path_rejects_non_target_file(tmp_path: Path) -> None:
    repo = tmp_path
    target = repo / "openspec/changes/visual/apply-design/APPLY-DESIGN.json"
    other = repo / "openspec/changes/visual/apply-design/OTHER.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")
    other.write_text("{}", encoding="utf-8")
    rel = "openspec/changes/visual/apply-design/OTHER.json"
    out = await resolve_submit_apply_design_content(
        json.dumps({"$from_path": rel}),
        repo=repo,
        target=target,
    )
    assert not out.ok
    assert out.code == "FORBIDDEN_PATH"


@pytest.mark.asyncio
async def test_from_path_loads_and_recomputes(tmp_path: Path) -> None:
    repo = tmp_path
    target = repo / "openspec/changes/visual/apply-design/APPLY-DESIGN.json"
    target.parent.mkdir(parents=True)
    body = {
        "schema": "opsx.apply_design",
        "schema_version": 3,
        "change_id": "visual",
        "status": "APPLY_DESIGN_READY",
    }
    target.write_text(json.dumps(body), encoding="utf-8")
    rel = "openspec/changes/visual/apply-design/APPLY-DESIGN.json"
    with patch(
        "agents.implementation.apply_design.submit_content_resolver._recompute_identities_on_staging",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_recompute:

        async def _fake_recompute(*, repo: Path, staging: Path) -> bool:
            staging.write_text(
                json.dumps({**body, "schema_version": 4}, indent=2) + "\n",
                encoding="utf-8",
            )
            return True

        mock_recompute.side_effect = _fake_recompute
        out = await resolve_submit_apply_design_content(
            json.dumps({"$from_path": rel}),
            repo=repo,
            target=target,
        )
    assert out.ok
    assert out.body is not None
    assert out.body.get("schema_version") == 4


@pytest.mark.asyncio
async def test_inline_recompute_identities_triggers_staging(tmp_path: Path) -> None:
    """``$recompute_identities`` на inline-теле включает Node recompute (без ``$from_path``)."""
    repo = tmp_path
    target = repo / "openspec/changes/visual/apply-design/APPLY-DESIGN.json"
    target.parent.mkdir(parents=True)
    payload = {
        "schema": "opsx.apply_design",
        "change_id": "visual",
        "status": "APPLY_DESIGN_READY",
        "planning_identity": {"inputs": [{"path": "openspec/changes/visual/proposal.md"}]},
        "$recompute_identities": True,
    }
    proposal = repo / "openspec/changes/visual/proposal.md"
    proposal.write_text("proposal\n", encoding="utf-8")
    with patch(
        "agents.implementation.apply_design.submit_content_resolver._recompute_identities_on_staging",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_recompute:

        async def _fake_recompute(*, repo: Path, staging: Path) -> bool:
            body = json.loads(staging.read_text(encoding="utf-8"))
            body["schema_version"] = 4
            body.setdefault("planning_identity", {})["planning_digest"] = "a" * 64
            staging.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
            return True

        mock_recompute.side_effect = _fake_recompute
        out = await resolve_submit_apply_design_content(
            json.dumps(payload),
            repo=repo,
            target=target,
        )
    assert out.ok
    assert mock_recompute.await_count == 1
    assert out.body is not None
    assert out.body.get("planning_identity", {}).get("planning_digest") == "a" * 64


@pytest.mark.asyncio
async def test_from_path_merges_identity_patch_and_strips_artifact_sha256(
    tmp_path: Path,
) -> None:
    """`$from_path` плюс patch не является INVALID_ARGS; sha256 файла не пишется в JSON."""
    repo = tmp_path
    rel = "openspec/changes/demo/apply-design/APPLY-DESIGN.json"
    target = repo / rel
    target.parent.mkdir(parents=True)
    canon = {
        "schema": "opsx.apply_design",
        "schema_version": 4,
        "change_id": "demo",
        "status": "APPLY_DESIGN_READY",
        "artifact_identity": {
            "human_path": "openspec/changes/demo/apply-design/APPLY-DESIGN.md",
            "machine_path": rel,
        },
    }
    target.write_text(json.dumps(canon), encoding="utf-8")
    payload = {
        "$from_path": rel,
        "$recompute_identities": True,
        "artifact_identity": {
            "human_path": canon["artifact_identity"]["human_path"],
            "machine_path": rel,
            "artifact_sha256": "ab" * 32,
        },
        "artifact_sha256": "cd" * 32,
    }
    with patch(
        "agents.implementation.apply_design.submit_content_resolver._recompute_identities_on_staging",
        new_callable=AsyncMock,
    ) as mock_recompute:

        async def _keep_staging(*, repo: Path, staging: Path) -> bool:
            return True

        mock_recompute.side_effect = _keep_staging
        out = await resolve_submit_apply_design_content(
            json.dumps(payload),
            repo=repo,
            target=target,
        )
    assert out.ok
    assert out.body is not None
    identity = out.body["artifact_identity"]
    assert "artifact_sha256" not in identity
    assert "artifact_sha256" not in out.body
    assert identity["machine_path"] == rel
    assert identity["human_path"] == canon["artifact_identity"]["human_path"]
    bare = await resolve_submit_apply_design_content(
        json.dumps({"$recompute_identities": True}),
        repo=repo,
        target=target,
    )
    assert bare.ok is False
    assert bare.code == "INVALID_ARGS"
    assert bare.field == "$from_path"
    assert bare.message is not None
    assert "artifact_sha256" in bare.message
