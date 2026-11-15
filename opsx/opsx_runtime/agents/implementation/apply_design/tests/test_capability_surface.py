"""Контракт capability surface apply-design без живой модели."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from agents.abc_agent import ABCAgent
from agents.adapters.abc_adapter import AgentResult, AgentRunContractError
from agents.adapters.capability_options import build_provider_agent_options
from agents.config import ReadScopeViolation
from agents.implementation.apply_design.capability import (
    INPUT_SCOPE_INCOMPLETE,
    build_apply_design_manifest,
)
from agents.implementation.apply_design.tool_runtime import ApplyDesignToolRuntime
from workflows.artifact_paths import apply_design_artifact_path


def _seed_change(root: Path, change_id: str) -> None:
    base = root / "openspec" / "changes" / change_id
    base.mkdir(parents=True)
    (base / "tasks.md").write_text("- [ ] t1\n", encoding="utf-8")
    (base / "design.md").write_text("# design\n", encoding="utf-8")


def _seed_capabilities(root: Path) -> None:
    cap = root / "opsx" / "project"
    cap.mkdir(parents=True)
    (cap / "capabilities.yaml").write_text(
        "product:\n  source_roots:\n    - src/stroy\n",
        encoding="utf-8",
    )
    src = root / "src" / "stroy"
    src.mkdir(parents=True)
    (src / "module.py").write_text("# design anchor\ndef f():\n    return 1\n", encoding="utf-8")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _seed_capabilities(tmp_path)
    _seed_change(tmp_path, "visual")
    _seed_change(tmp_path, "other-change")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "t.py").write_text("def test_x(): pass\n", encoding="utf-8")
  # deny tree
    (tmp_path / "opsx" / "logs" / "secret.log").parent.mkdir(parents=True)
    (tmp_path / "opsx" / "logs" / "secret.log").write_text("x", encoding="utf-8")
    return tmp_path


def test_options_only_custom_tools(repo: Path) -> None:
    manifest = build_apply_design_manifest("visual", repo_root=repo)
    opts = build_provider_agent_options(
        manifest,
        api_key="k",
        model={"id": "x"},
        local={"cwd": str(repo)},
    )
    assert opts["tools"] == ["mcp"]
    custom = opts["local"]["custom_tools"]
    assert set(custom.keys()) == {
        "search_repository",
        "read_source",
        "submit_apply_design",
    }
    for name in ("shell", "grep", "glob", "ls", "read", "edit"):
        assert name not in custom
    assert opts.get("mcpServers") == {}
    assert opts["local"].get("setting_sources") == []


def test_resume_options_match_create(repo: Path) -> None:
    manifest = build_apply_design_manifest("visual", repo_root=repo)
    opts1 = build_provider_agent_options(
        manifest, api_key="k", model={"id": "m"}, local={"cwd": str(repo)}
    )
    opts2 = build_provider_agent_options(
        manifest, api_key="k", model={"id": "m"}, local={"cwd": str(repo)}
    )
    assert opts1["tools"] == opts2["tools"] == ["mcp"]
    assert set(opts1["local"]["custom_tools"]) == set(opts2["local"]["custom_tools"])


@pytest.mark.asyncio
async def test_read_current_change_planning(repo: Path) -> None:
    manifest = build_apply_design_manifest("visual", repo_root=repo)
    rt = ApplyDesignToolRuntime(manifest)
    out = await rt.read_source({"source_id": "planning:tasks_md"}, None)
    assert out.get("ok") is True
    assert "t1" in str(out.get("fragment"))
    assert out.get("eof") is True


@pytest.mark.asyncio
async def test_read_other_change_via_search_id(repo: Path) -> None:
    other = repo / "openspec" / "changes" / "other-change" / "design.md"
    manifest = build_apply_design_manifest("visual", repo_root=repo)
    rel = other.relative_to(repo).as_posix()
    sid = f"changes:{rel}"
    rt = ApplyDesignToolRuntime(manifest)
    out = await rt.read_source({"source_id": sid}, None)
    assert out.get("ok") is True


@pytest.mark.asyncio
async def test_unknown_source_rejected(repo: Path) -> None:
    manifest = build_apply_design_manifest("visual", repo_root=repo)
    rt = ApplyDesignToolRuntime(manifest)
    out = await rt.read_source({"source_id": "nope"}, None)
    assert out.get("code") == "UNKNOWN_SOURCE"


@pytest.mark.asyncio
async def test_submit_writes_only_current_change(repo: Path) -> None:
    from unittest.mock import AsyncMock, patch

    from workflows.implementation.design_machine_validation import (
        DesignDependentSnapshotResult,
        DesignMachineGateResult,
    )

    manifest = build_apply_design_manifest("visual", repo_root=repo)
    assert manifest.design_submission is not None
    payload = json.dumps(
        {
            "schema": "opsx.apply_design",
            "schema_version": 4,
            "change_id": "visual",
            "status": "APPLY_DESIGN_READY",
            "generated_at": "2026-11-12T00:00:00Z",
            "chat_history_required": False,
            "write_boundary": {"allowed_paths": []},
            "artifact_identity": {"machine_path": "openspec/changes/visual/apply-design/APPLY-DESIGN.json"},
            "next_stage": "APPLY_DESIGN_REVIEW",
            "planning_identity": {"planning_digest": "p"},
            "protected_context_identity": {"content_digest": "c"},
            "reuse_decisions": [{"decision": "new"}],
            "composition_completeness": {"invariant_classifications": [{"id": "x"}]},
            "blockers": [],
            "slices": [],
        }
    )
    rt = ApplyDesignToolRuntime(manifest)
    with patch(
        "agents.implementation.apply_design.tool_runtime.validate_apply_design_at_path_async",
        new_callable=AsyncMock,
        return_value=DesignMachineGateResult(ok=True, status="PASS", exit_code=0, errors=[]),
    ), patch(
        "agents.implementation.apply_design.tool_runtime.evaluate_apply_design_snapshot_from_body_async",
        new_callable=AsyncMock,
        return_value=DesignDependentSnapshotResult(
            ok=True, code=None, frozen_digest="f", current_digest="f"
        ),
    ):
        out = await rt.submit_apply_design(
            {"content": payload, "base_digest": manifest.submission_digest()},
            None,
        )
    assert out.get("ok") is True
    path = apply_design_artifact_path(repo, "visual")
    assert path.is_file()
    other = apply_design_artifact_path(repo, "other-change")
    assert not other.is_file()


@pytest.mark.asyncio
async def test_search_finds_product_and_changes(repo: Path) -> None:
    manifest = build_apply_design_manifest("visual", repo_root=repo)
    rt = ApplyDesignToolRuntime(manifest)
    out = await rt.search_repository(
        {"query": "design", "domains": ["product", "changes"]},
        None,
    )
    assert out.get("ok") is True
    hits = out.get("hits") or []
    domains = {h["source_id"].split(":", 1)[0] for h in hits}
    assert "product" in domains
    assert "changes" in domains


@pytest.mark.asyncio
async def test_search_skips_opsx_logs(repo: Path) -> None:
    manifest = build_apply_design_manifest("visual", repo_root=repo)
    rt = ApplyDesignToolRuntime(manifest)
    out = await rt.search_repository(
        {"query": "secret", "domains": ["product", "changes", "tests"]},
        None,
    )
    hits = out.get("hits") or []
    assert not any("opsx/logs" in h["source_id"] for h in hits)


def test_incomplete_planning_manifest(repo: Path) -> None:
    incomplete = repo / "openspec" / "changes" / "empty"
    incomplete.mkdir(parents=True)
    manifest = build_apply_design_manifest("empty", repo_root=repo)
    assert not manifest.planning_complete
    assert INPUT_SCOPE_INCOMPLETE  # constant exists


def test_custom_tool_schemas_have_no_path() -> None:
    manifest = build_apply_design_manifest(
        "visual",
        repo_root=Path("C:/fake"),
    )
    # use minimal manifest only for options build with fake paths — skip file ops
    from agents.capabilities.manifest import AgentCapabilityManifest, DesignSubmission

    sub = DesignSubmission("visual", Path("/a"), Path("/b"), "")
    m = AgentCapabilityManifest(
        change_id="visual",
        repo_root=Path("/r"),
        read_domains={},
        readable_sources={},
        prior_designs={},
        design_submission=sub,
    )
    opts = build_provider_agent_options(
        m, api_key="k", model={"id": "x"}, local={"cwd": "/r"}
    )
    for tool in opts["local"]["custom_tools"].values():
        schema = json.dumps(tool.input_schema or {})
        assert "path" not in schema
        assert "command" not in schema
        assert "cwd" not in schema


@pytest.mark.asyncio
async def test_submit_digest_conflict(repo: Path) -> None:
    manifest = build_apply_design_manifest("visual", repo_root=repo)
    rt = ApplyDesignToolRuntime(manifest)
    out = await rt.submit_apply_design(
        {"content": "{}", "base_digest": "deadbeef"},
        None,
    )
    assert out.get("code") == "DESIGN_DIGEST_CONFLICT"


def test_execute_handlers_are_async(repo: Path) -> None:
    manifest = build_apply_design_manifest("visual", repo_root=repo)
    rt = ApplyDesignToolRuntime(manifest)
    assert inspect.iscoroutinefunction(rt.search_repository)
    assert inspect.iscoroutinefunction(rt.read_source)
    assert inspect.iscoroutinefunction(rt.submit_apply_design)


@pytest.mark.asyncio
async def test_capability_hop_scope_violation_no_resume() -> None:
    class _Adapter:
        calls = 0

        async def _run_turn(self, state, **kwargs):
            _Adapter.calls += 1
            raise ReadScopeViolation(
                tool="shell",
                path="x",
                rule="test",
            )

    adapter = _Adapter()
    state = {"repository_path": "C:/repo", "model": {"id": "m"}, "capability_manifest": object()}
    with pytest.raises(AgentRunContractError) as err:
        await ABCAgent.submit_turn(
            adapter,  # type: ignore[arg-type]
            state,
            prompt="p",
            tools=(),
            read_scope=None,
            resume_agent_id=None,
        )
    assert err.value.code == "TOOL_SCOPE_VIOLATION"
    assert _Adapter.calls == 1


def test_resolve_domain_path_rejects_escape(repo: Path) -> None:
    from agents.capabilities.path_policy import resolve_domain_path

    assert resolve_domain_path("changes:openspec/changes/visual/../../.env", repo) is None
    assert resolve_domain_path("product:src/stroy/../../../windows/system.ini", repo) is None


@pytest.mark.asyncio
async def test_prior_designs_search_card(repo: Path) -> None:
    rec_dir = (
        repo / "openspec" / "changes" / "legacy-design" / "apply-design"
    )
    rec_dir.mkdir(parents=True)
    (rec_dir / "APPLY-DESIGN.json").write_text(
        '{"status":"APPLY_DESIGN_READY","summary":"slice plan alpha"}',
        encoding="utf-8",
    )
    manifest = build_apply_design_manifest("visual", repo_root=repo)
    assert "prior_design:legacy-design" in manifest.prior_designs
    rt = ApplyDesignToolRuntime(manifest)
    out = await rt.search_repository(
        {"query": "legacy-design", "domains": ["prior_designs"]},
        None,
    )
    assert out.get("ok") is True
    hits = out.get("hits") or []
    assert any(h["source_id"] == "prior_design:legacy-design" for h in hits)


def test_two_manifests_do_not_share_submission(repo: Path) -> None:
    m1 = build_apply_design_manifest("visual", repo_root=repo)
    m2 = build_apply_design_manifest("other-change", repo_root=repo)
    assert m1.design_submission is not None
    assert m2.design_submission is not None
    assert m1.design_submission.json_path != m2.design_submission.json_path
