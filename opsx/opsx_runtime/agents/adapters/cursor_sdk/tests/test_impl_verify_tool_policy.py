"""Регрессия: impl_verify — read-only tools и pre-scope custom read."""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.config import check_paths_in_scope, resolve_read_scope
from agents.verify.impl_verify.agent import _IMPL_VERIFY_TOOLS
from agents.verify.scenario_coverage_verify.row_single import _READ_TOOLS as _SCENARIO_COVERAGE_READ_TOOLS
from agents.implementation.apply.agent import _APPLY_TOOLS
from agents.repair.helpers import GREEN_VERIFIER_TOOLS
from agents.verify.impl_verify.scoped_read_tool import (
    ImplVerifyScopedReadRuntime,
    build_impl_verify_scoped_read_sdk_options,
    hop_uses_scoped_read_tool,
    impl_verify_uses_scoped_read_tool,
    scoped_read_result_text,
)
from cursor_sdk._tool_callback import _normalize_custom_tool_result

_REPO = next(p for p in Path(__file__).resolve().parents if (p / "SOURCE_EXPORT.json").is_file())


def test_green_verifier_hop_uses_scoped_read_with_run_pytest_mcp() -> None:
    """GREEN_VERIFIER: scoped read + MCP run_pytest, без disallowed mcp."""
    from agents.agent_tools.deterministic.registry import hop_includes_run_pytest_tool

    assert hop_uses_scoped_read_tool("GREEN_VERIFIER", GREEN_VERIFIER_TOOLS)
    assert hop_includes_run_pytest_tool("GREEN_VERIFIER")
    scope = resolve_read_scope("GREEN_VERIFIER")
    options = build_impl_verify_scoped_read_sdk_options(
        api_key="test",
        model={"slug": "composer-2.5"},
        local={},
        scope=scope,
        repo_root=_REPO,
        include_run_pytest=True,
    )
    disallowed = frozenset(str(tool) for tool in options["disallowed_tools"])
    assert "mcp" not in disallowed
    assert "mcp" in options["tools"]
    custom = options["local"]["custom_tools"]
    assert "run_pytest" in custom


def test_impl_verify_tool_list_is_read_only() -> None:
    """Producer: hop не отдаёт grep/glob/ls — иначе модель снова бьётся о read_scope."""
    forbidden = frozenset({"grep", "glob", "ls", "list_dir", "listdir", "search"})
    assert _IMPL_VERIFY_TOOLS == ("read",)
    assert not forbidden.intersection(_IMPL_VERIFY_TOOLS)


def test_impl_verify_scope_parent_openspec_grep_stays_blocked() -> None:
    """Guard: grep по parent ``openspec/`` остаётся outside_restrict_allow."""
    scope = resolve_read_scope("impl_verify")
    result = check_paths_in_scope(
        scope,
        tool_name="grep",
        paths=["openspec"],
        repo_root=_REPO,
    )
    assert result.violations
    assert result.violations[0].rule == "outside_restrict_allow"


def test_impl_verify_sdk_disallowed_tools_only_valid_cursor_names() -> None:
    """CreateAgent падает на ``list_dir``/``listdir`` — только SDK-имена в disallow."""
    invalid_sdk_aliases = frozenset({"list_dir", "listdir"})
    scope = resolve_read_scope("impl_verify")
    options = build_impl_verify_scoped_read_sdk_options(
        api_key="test",
        model={"slug": "composer-2.5"},
        local={},
        scope=scope,
        repo_root=_REPO,
    )
    disallowed = frozenset(str(t) for t in options["disallowed_tools"])
    assert not invalid_sdk_aliases.intersection(disallowed)
    assert "read" in disallowed
    assert "ls" in disallowed


def test_impl_verify_uses_scoped_read_only_for_read_tool_hop() -> None:
    """Custom read wiring только для impl_verify + единственный ``read``."""
    assert impl_verify_uses_scoped_read_tool("impl_verify", ("read",))
    assert not impl_verify_uses_scoped_read_tool("impl_verify", ("read", "grep"))
    assert not impl_verify_uses_scoped_read_tool("apply_scaffold", ("read",))


def test_scenario_coverage_verify_read_tools_match_impl_verify_policy() -> None:
    """Producer: row audit hop — только scoped ``read``, без repo search tools."""
    forbidden = frozenset({"grep", "glob", "ls", "list_dir", "listdir", "search"})
    assert _SCENARIO_COVERAGE_READ_TOOLS == ("read",)
    assert not forbidden.intersection(_SCENARIO_COVERAGE_READ_TOOLS)
    assert hop_uses_scoped_read_tool("scenario_coverage_verify", _SCENARIO_COVERAGE_READ_TOOLS)


def test_verify_apply_tools_exclude_repo_search() -> None:
    """Verify ``apply`` hop не отдаёт grep/glob/ls/shell (regression: READ_SCOPE_EXHAUSTED)."""
    forbidden = frozenset({"grep", "glob", "ls", "shell", "list_dir", "listdir", "search"})
    assert not forbidden.intersection(_APPLY_TOOLS)
    assert hop_uses_scoped_read_tool("apply", _APPLY_TOOLS)


def test_apply_scoped_read_sdk_disallows_builtin_search_tools(monkeypatch) -> None:
    """``apply``: custom read + edit; built-in read/grep/glob/ls/shell off."""
    monkeypatch.setenv("OPSX_PROJECT_VISUAL_ASSET_MODULE", "opsx.integrations.stroy_system.visual_cover_asset_tool")
    scope = resolve_read_scope("apply")
    options = build_impl_verify_scoped_read_sdk_options(
        api_key="test",
        model={"slug": "composer-2.5"},
        local={},
        scope=scope,
        repo_root=_REPO,
        extra_builtin_tools=("edit",),
        include_visual_cover_asset=True,
    )
    disallowed = frozenset(str(t) for t in options["disallowed_tools"])
    assert disallowed == frozenset({"read", "grep", "glob", "ls", "shell"})
    assert frozenset(options["tools"]) == frozenset({"mcp", "edit"})
    custom = options["local"]["custom_tools"]
    assert "visual_cover_asset" in custom


@pytest.mark.asyncio
async def test_impl_verify_scoped_read_rejects_repo_root_and_allows_change_path(tmp_path) -> None:
    """An undeclared root file is rejected; declared project changes remain readable."""
    scope = resolve_read_scope("impl_verify")
    runtime = ImplVerifyScopedReadRuntime(scope=scope, repo_root=tmp_path)
    denied = await runtime.read({"path": "private-settings.ini"}, None)
    assert denied.get("code") == "READ_SCOPE_VIOLATION"
    assert denied.get("rule") == "outside_restrict_allow"
    parent_openspec = await runtime.read({"path": "openspec"}, None)
    assert parent_openspec.get("code") == "READ_SCOPE_VIOLATION"
    path = tmp_path / "openspec/changes/example/tasks.md"
    path.parent.mkdir(parents=True)
    path.write_text("Example task", encoding="utf-8")
    allowed = await runtime.read({"path": "openspec/changes/example/tasks.md"}, None)
    assert allowed.get("ok") is True
    assert "Example task" in scoped_read_result_text(allowed)


@pytest.mark.asyncio
async def test_impl_verify_scoped_read_mcp_wire_preserves_large_unicode_source() -> None:
    """Большой Unicode source не теряется после SDK ``_normalize_custom_tool_result``."""
    scope = resolve_read_scope("impl_verify")
    runtime = ImplVerifyScopedReadRuntime(scope=scope, repo_root=_REPO)
    target = _REPO / "src/stroy/channels/telegram/invest_yalta/telegram_present.py"
    if not target.is_file():
        pytest.skip("telegram_present.py missing in workspace")

    raw = await runtime.read(
        {"path": "src/stroy/channels/telegram/invest_yalta/telegram_present.py"},
        None,
    )
    assert raw.get("ok") is True
    assert raw.get("fileSize", -1) > 0
    wired = _normalize_custom_tool_result(raw)
    content = wired.get("content")
    assert isinstance(content, list), "dict с content:str не проходит MCP-wire; нужен content[]"
    wire_text = scoped_read_result_text(wired)
    assert len(wire_text) > 30_000
    assert "confirmed_screen_caption" in wire_text
    assert "\n" in wire_text


@pytest.mark.asyncio
async def test_impl_verify_scoped_read_auto_paginates_oversized_full_body() -> None:
    """Файлы >64KiB без offset/limit не отдают пустой wire-text — первая страница + hint."""
    scope = resolve_read_scope("impl_verify")
    runtime = ImplVerifyScopedReadRuntime(scope=scope, repo_root=_REPO)
    target = _REPO / "src/stroy/consultation/invest_yalta/handler.py"
    if not target.is_file():
        pytest.skip("handler.py missing in workspace")

    raw = await runtime.read({"path": "src/stroy/consultation/invest_yalta/handler.py"}, None)
    assert raw.get("ok") is True
    assert raw.get("paginationRequired") is True
    assert raw.get("numbered") is True
    wired = _normalize_custom_tool_result(raw)
    wire_text = scoped_read_result_text(wired)
    assert len(wire_text) > 500
    assert "use offset/limit" in wire_text
    assert "run_invest_yalta_funnel" in wire_text or "   1|" in wire_text


@pytest.mark.asyncio
async def test_impl_verify_scoped_read_numbered_paging_keeps_absolute_lines(tmp_path) -> None:
    scope = resolve_read_scope("scenario_coverage_verify")
    repo = tmp_path
    target = repo / "tests" / "sample.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        "line1\nline2\nline3\nassert True\n",
        encoding="utf-8",
    )
    runtime = ImplVerifyScopedReadRuntime(scope=scope, repo_root=repo)
    first = await runtime.read(
        {"path": "tests/sample.py", "offset": 1, "limit": 2},
        None,
    )
    assert first.get("ok") is True
    assert first.get("numbered") is True
    assert first.get("offset") == 1
    assert first.get("endLine") == 2
    text = scoped_read_result_text(first)
    assert "   1| line1" in text or "1| line1" in text
    assert "2| line2" in text
    revision = str(first.get("source_revision"))
    second = await runtime.read(
        {
            "path": "tests/sample.py",
            "offset": 3,
            "limit": 2,
            "expected_revision": revision,
        },
        None,
    )
    assert second.get("ok") is True
    page2 = scoped_read_result_text(second)
    assert "3| line3" in page2
    assert "4| assert True" in page2


@pytest.mark.asyncio
async def test_impl_verify_scoped_read_error_and_empty_shapes_are_distinct(tmp_path) -> None:
    """missing/denied/empty не смешиваются с непустым успехом на wire."""
    scope = resolve_read_scope("impl_verify")
    runtime = ImplVerifyScopedReadRuntime(scope=scope, repo_root=tmp_path)

    denied = await runtime.read({"path": "private-settings.ini"}, None)
    assert denied.get("isError") is True
    assert denied.get("code") == "READ_SCOPE_VIOLATION"
    assert scoped_read_result_text(denied)

    missing = await runtime.read(
        {"path": "openspec/changes/visual/no-such-file.txt"},
        None,
    )
    assert missing.get("code") == "MISSING_FILE"
    assert missing.get("isError") is True

    empty_path = tmp_path / "tests" / "_scoped_read_empty_fixture.txt"
    empty_path.parent.mkdir(parents=True)
    empty_path.write_text("", encoding="utf-8")
    try:
        empty = await runtime.read(
            {"path": "tests/_scoped_read_empty_fixture.txt"},
            None,
        )
    finally:
        empty_path.unlink(missing_ok=True)

    assert empty.get("ok") is True
    assert empty.get("emptyFile") is True
    assert empty.get("isError") is not True
    assert scoped_read_result_text(empty) == ""


def test_prompt_has_no_repo_root_read_ban() -> None:
    """Промпт не дублирует tool-level запрет repo-root read."""
    from agents.verify.impl_verify.prompt.get_prompt import get_prompt

    text = get_prompt(
        repository_path=str(_REPO),
        change_id="visual",
        artifact_bundle={"spec_paths": []},
    )
    assert "alembic.ini" not in text
    assert "файлов в корне репозитория" not in text


def test_impl_verify_prompt_lists_declared_project_migration_path(tmp_path) -> None:
    """PHASE_IMPL prompt включает declared migration path (без grep по versions/)."""
    from agents.verify.impl_verify.preflight import load_change_artifact_bundle
    from agents.verify.impl_verify.prompt.get_prompt import get_prompt

    change = tmp_path / "openspec/changes/example"
    change.mkdir(parents=True)
    (change / "design.md").write_text("Migration: `migrations/versions/0001_example.py`", encoding="utf-8")
    bundle = load_change_artifact_bundle("example", repo_root=tmp_path)
    text = get_prompt(repository_path=str(tmp_path), change_id="example", artifact_bundle=bundle)
    assert "migrations/versions/0001_example.py" in text
