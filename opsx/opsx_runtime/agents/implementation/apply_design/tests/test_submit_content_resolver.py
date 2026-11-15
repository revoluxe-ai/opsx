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
