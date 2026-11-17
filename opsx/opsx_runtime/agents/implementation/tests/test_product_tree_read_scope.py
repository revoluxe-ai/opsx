"""Product trees beyond hardcoded ``src/**`` stay inside slice and repair scope."""

from __future__ import annotations

import json
from pathlib import Path

from agents.config import (
    check_paths_in_scope,
    path_allowed_by_scope,
    resolve_effective_read_scope,
    resolve_read_scope,
)


def test_slice_and_repair_allow_app_tree_and_declared_source_roots(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    (repo / "opsx").mkdir(parents=True)
    design = (
        repo
        / "openspec"
        / "changes"
        / "sample-product"
        / "apply-design"
        / "APPLY-DESIGN.json"
    )
    design.parent.mkdir(parents=True)
    design.write_text(
        json.dumps(
            {
                "slices": [
                    {
                        "expected_product_write_surfaces": [
                            "services/billing/meter.py"
                        ],
                        "relevant_existing_surfaces": [
                            "opsx/opsx_runtime/agents/config.py"
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "OPSX_PRODUCT_SOURCE_ROOTS_JSON",
        json.dumps(["backend/pkg", "opsx/opsx_runtime"]),
    )

    scaffold = resolve_read_scope("apply_scaffold")
    assert path_allowed_by_scope(
        scaffold, "app/pkg/module.py", repo_root=repo
    )
    assert path_allowed_by_scope(
        scaffold, "src/pkg/module.py", repo_root=repo
    )
    assert path_allowed_by_scope(
        scaffold, "backend/pkg/mod.py", repo_root=repo
    )
    assert not path_allowed_by_scope(
        scaffold, "opsx/opsx_runtime/agents/config.py", repo_root=repo
    )
    assert not path_allowed_by_scope(
        scaffold, ".github/workflows/ci.yml", repo_root=repo
    )

    state = {
        "change_id": "sample-product",
        "implementation_slice_id": "slice-1",
        "repository_path": str(repo),
    }
    effective = resolve_effective_read_scope("apply_scaffold", state)
    assert not check_paths_in_scope(
        effective,
        tool_name="read",
        paths=["services/billing/meter.py"],
        repo_root=repo,
    ).violations
    assert check_paths_in_scope(
        effective,
        tool_name="read",
        paths=["opsx/opsx_runtime/agents/config.py"],
        repo_root=repo,
    ).violations

    repair = resolve_read_scope("IMPLEMENTATION_EXECUTOR")
    assert not check_paths_in_scope(
        repair,
        tool_name="edit",
        paths=["app/pkg/module.py"],
        repo_root=repo,
    ).violations
    assert check_paths_in_scope(
        repair,
        tool_name="edit",
        paths=["migrations/env.py"],
        repo_root=repo,
    ).violations
