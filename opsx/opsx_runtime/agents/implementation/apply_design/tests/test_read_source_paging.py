"""Lossless paging и InputPack для apply-design read_source."""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.implementation.apply_design.capability import build_apply_design_manifest
from agents.implementation.apply_design.prompt.build_input_pack import (
    build_apply_design_input_pack,
)
from agents.implementation.apply_design.prompt.get_prompt import get_prompt_sync
from agents.implementation.apply_design.read_source import (
    READ_SOURCE_PAGE_BYTES,
    execute_read_source,
)
from agents.implementation.apply_design.tool_runtime import ApplyDesignToolRuntime


def _seed(repo: Path, change_id: str = "visual") -> None:
    base = repo / "openspec" / "changes" / change_id
    base.mkdir(parents=True, exist_ok=True)
    (base / "tasks.md").write_text("- [ ] t1\n", encoding="utf-8")
    (base / "design.md").write_text("# design\n", encoding="utf-8")
    cap = repo / "opsx" / "project"
    cap.mkdir(parents=True, exist_ok=True)
    (cap / "capabilities.yaml").write_text(
        "product:\n  source_roots:\n    - src/stroy\n",
        encoding="utf-8",
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _seed(tmp_path)
    deny = tmp_path / "opsx" / "logs"
    deny.mkdir(parents=True)
    (deny / "secret.log").write_text("secret", encoding="utf-8")
    return tmp_path


@pytest.mark.asyncio
async def test_unknown_vs_missing_vs_empty_vs_forbidden(repo: Path) -> None:
    manifest = build_apply_design_manifest("visual", repo_root=repo)
    rt = ApplyDesignToolRuntime(manifest)

    unknown = await rt.read_source({"source_id": "nope"}, None)
    assert unknown.get("code") == "UNKNOWN_SOURCE"

    forbidden = await rt.read_source({"source_id": "product:opsx/logs/secret.log"}, None)
    assert forbidden.get("code") == "FORBIDDEN_PATH"

    missing = await rt.read_source({"source_id": "planning:owner_contract_md"}, None)
    assert missing.get("code") == "MISSING_FILE"

    empty_dir = repo / "openspec" / "changes" / "visual"
    empty_file = empty_dir / "empty-note.md"
    empty_file.write_bytes(b"")
    rel = empty_file.relative_to(repo).as_posix()
    sid = f"changes:{rel}"
    out = await rt.read_source({"source_id": sid}, None)
    assert out.get("ok") is True
    assert out.get("total_size") == 0
    assert out.get("fragment") == ""
    assert out.get("eof") is True


@pytest.mark.asyncio
async def test_lossless_paging_reconstructs_bytes(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "agents.implementation.apply_design.read_source.READ_SOURCE_PAGE_BYTES",
        64,
    )
    payload = "α" * 40 + "β" * 80 + "γ" * 100
    blob_path = repo / "src" / "stroy"
    blob_path.mkdir(parents=True)
    big = blob_path / "big_module.py"
    big.write_text(payload, encoding="utf-8")
    rel = big.relative_to(repo).as_posix()
    sid = f"product:{rel}"

    manifest = build_apply_design_manifest("visual", repo_root=repo)
    rt = ApplyDesignToolRuntime(manifest)
    parts: list[str] = []
    cursor = 0
    revision: str | None = None
    while True:
        args: dict[str, object] = {"source_id": sid, "cursor": cursor}
        if cursor > 0:
            assert revision is not None
            args["expected_revision"] = revision
        page = await rt.read_source(args, None)
        assert page.get("ok") is True
        if revision is None:
            revision = str(page.get("source_revision"))
        else:
            assert page.get("source_revision") == revision
        parts.append(str(page.get("fragment")))
        if page.get("eof"):
            break
        cursor = int(page["next_cursor"])
    assert "".join(parts) == payload


@pytest.mark.asyncio
async def test_prompt_includes_manifest_source_ids(repo: Path) -> None:
    manifest = build_apply_design_manifest("visual", repo_root=repo)
    pack = await build_apply_design_input_pack(manifest)
    prompt = get_prompt_sync(
        repository_path=str(repo),
        change_id="visual",
        impl_verify_answer={},
        artifact_bundle={},
        machine_context={},
        input_pack=pack,
    )
    assert "planning:tasks_md" in prompt
    assert "planning:design_md" in prompt
    assert "Apply-design InputPack" in prompt
    assert "search_repository" in prompt
    assert "do not invent aliases" in prompt
