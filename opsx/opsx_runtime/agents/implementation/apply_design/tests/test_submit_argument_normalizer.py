"""submit_apply_design принимает объект и директиву с patch без INVALID_ARGS."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.adapters.agent_capability_options import build_provider_agent_options
from agents.capabilities.manifest import AgentCapabilityManifest, DesignSubmission
from agents.implementation.apply_design.submit_argument_normalizer import (
    coerce_submit_content,
)


def test_submit_schema_accepts_object_content_without_path_argument() -> None:
    sub = DesignSubmission("demo", Path("/a"), Path("/b"), "")
    manifest = AgentCapabilityManifest(
        change_id="demo",
        repo_root=Path("/r"),
        read_domains={},
        readable_sources={},
        prior_designs={},
        design_submission=sub,
    )

    class _Runtime:
        def __init__(self) -> None:
            self._repo = Path("/r")
            self._manifest = SimpleNamespace(design_submission=sub)

        async def search_repository(self, args, _ctx):
            return {"ok": True}

        async def read_source(self, args, _ctx):
            return {"ok": True}

        async def submit_apply_design(self, args, _ctx):
            return {"ok": True, "args": args}

    opts = build_provider_agent_options(
        manifest,
        api_key="k",
        model={"id": "x"},
        local={"cwd": "/r"},
        tool_runtime=_Runtime(),
    )
    schema = opts["local"]["custom_tools"]["submit_apply_design"].input_schema
    encoded = json.dumps(schema)
    assert "path" not in encoded
    assert "command" not in encoded
    variants = schema["properties"]["content"]["anyOf"]
    assert {"type": "object"} in variants
    assert any(item.get("type") == "string" for item in variants)


@pytest.mark.asyncio
async def test_from_path_object_merges_patch_and_strips_artifact_sha256(
    tmp_path: Path,
) -> None:
    rel = "openspec/changes/demo/apply-design/APPLY-DESIGN.json"
    target = tmp_path / rel
    target.parent.mkdir(parents=True)
    canon = {
        "schema": "opsx.apply_design",
        "schema_version": 4,
        "change_id": "demo",
        "status": "APPLY_DESIGN_READY",
        "summary": "kept",
        "artifact_identity": {
            "human_path": "openspec/changes/demo/apply-design/APPLY-DESIGN.md",
            "machine_path": rel,
        },
    }
    target.write_text(json.dumps(canon), encoding="utf-8")
    content = {
        "$from_path": rel,
        "summary": "patched",
        "artifact_sha256": "ab" * 32,
        "artifact_identity": {
            "machine_path": rel,
            "artifact_sha256": "cd" * 32,
        },
    }
    text = coerce_submit_content(content, repo=tmp_path, target=target)
    assert isinstance(text, str)
    body = json.loads(text)
    assert body["summary"] == "patched"
    assert body["status"] == "APPLY_DESIGN_READY"
    assert "$from_path" not in body
    assert "artifact_sha256" not in body
    assert "artifact_sha256" not in body["artifact_identity"]
    assert body["artifact_identity"]["human_path"] == canon["artifact_identity"]["human_path"]
    assert body["$recompute_identities"] is True

    sub = DesignSubmission("demo", target, target, "")

    class _Runtime:
        def __init__(self) -> None:
            self._repo = tmp_path
            self._manifest = SimpleNamespace(design_submission=sub)
            self.seen = None

        async def search_repository(self, args, _ctx):
            return {"ok": True}

        async def read_source(self, args, _ctx):
            return {"ok": True}

        async def submit_apply_design(self, args, _ctx):
            self.seen = args
            return {"ok": True}

    runtime = _Runtime()
    manifest = AgentCapabilityManifest(
        change_id="demo",
        repo_root=tmp_path,
        read_domains={},
        readable_sources={},
        prior_designs={},
        design_submission=sub,
    )
    opts = build_provider_agent_options(
        manifest,
        api_key="k",
        model={"id": "x"},
        local={"cwd": str(tmp_path)},
        tool_runtime=runtime,
    )
    execute = opts["local"]["custom_tools"]["submit_apply_design"].execute
    out = await execute({"content": content, "base_digest": ""}, None)
    assert out["ok"] is True
    assert isinstance(runtime.seen["content"], str)
    seen = json.loads(runtime.seen["content"])
    assert "artifact_sha256" not in seen
    assert seen["summary"] == "patched"
