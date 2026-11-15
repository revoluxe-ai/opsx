"""Тесты политики read-scope (config + stream gate helpers)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agents.config import (
    AGENT_READ_OVERRIDES,
    AgentReadOverride,
    GLOBAL_READ_SCOPE,
    ReadScopeViolation,
    assert_paths_in_scope_from_record,
    check_paths_in_scope,
    path_allowed_by_scope,
    paths_from_shell_command,
    paths_from_tool_args,
    read_scope_enforcement_mode,
    resolve_read_scope,
    to_repo_relative,
)
from agents.adapters.cursor_sdk.cursor_sdk_stream import CursorRunStreamSink

_REPO = Path("C:/Users/vkita/my_code/opsx-implement")
_FIXTURES = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "read_scope_cases.jsonl"
)


def test_global_deny_blocks_logs_and_opsx_runs() -> None:
    scope = resolve_read_scope(None)
    assert not path_allowed_by_scope(scope, "foo.live.log", repo_root=_REPO)
    assert not path_allowed_by_scope(
        scope, "opsx/runs/verify/x/checkpoints.sqlite", repo_root=_REPO
    )
    assert not path_allowed_by_scope(
        scope,
        "opsx/opsx_runtime/run_dialogs/x/conversation.md",
        repo_root=_REPO,
    )


def test_restrict_allow_permits_product_tree() -> None:
    scope = resolve_read_scope(None)
    assert path_allowed_by_scope(scope, "src/stroy/config.py", repo_root=_REPO)
    assert not path_allowed_by_scope(
        scope, "opsx/opsx_runtime/agents/config.py", repo_root=_REPO
    )
    assert path_allowed_by_scope(scope, "migrations/versions/abc.py", repo_root=_REPO)
    assert not path_allowed_by_scope(scope, ".cursor/pipeline/foo.md", repo_root=_REPO)
    assert not path_allowed_by_scope(scope, "opsx_tools/opsx-apply-green.mjs", repo_root=_REPO)


def test_restrict_allow_blocks_arbitrary_repo_dirs() -> None:
    scope = resolve_read_scope(None)
    assert not path_allowed_by_scope(scope, ".cursor/rules/foo.mdc", repo_root=_REPO)
    assert not path_allowed_by_scope(
        scope, ".cursor/rules/testing-rules.mdc", repo_root=_REPO
    )
    assert not path_allowed_by_scope(scope, ".github/workflows/ci.yml", repo_root=_REPO)
    assert not path_allowed_by_scope(
        scope,
        "opsx/logs/visual/deadbeef.log",
        repo_root=_REPO,
    )
    assert not path_allowed_by_scope(
        scope,
        "opsx/receipts/visual/IMPL-VERIFY-RECEIPT.json",
        repo_root=_REPO,
    )


def test_dot_opsx_runs_denied() -> None:
    scope = resolve_read_scope(None)
    assert not path_allowed_by_scope(scope, ".opsx/runs/foo/bar", repo_root=_REPO)


def test_repair_product_hops_product_surface() -> None:
    from agents.config import check_paths_in_scope
    from agents.repair.product_surface import repair_product_write_globs

    for key in ("IMPLEMENTATION_EXECUTOR", "RED_TEST_WORKER"):
        scope = resolve_read_scope(key)
        assert path_allowed_by_scope(scope, "src/stroy/config.py", repo_root=_REPO)
        assert path_allowed_by_scope(
            scope,
            "tests/unit/consultation/foo.py",
            repo_root=_REPO,
        )
        assert path_allowed_by_scope(
            scope,
            "migrations/versions/0017_foo.py",
            repo_root=_REPO,
        )
        assert path_allowed_by_scope(scope, "migrations/env.py", repo_root=_REPO)
        assert path_allowed_by_scope(scope, "alembic.ini", repo_root=_REPO)
        assert path_allowed_by_scope(scope, "pyproject.toml", repo_root=_REPO)
        assert not path_allowed_by_scope(
            scope,
            "openspec/changes/visual/design.md",
            repo_root=_REPO,
        )
        mig = "migrations/versions/0017_foo.py"
        read_ok = check_paths_in_scope(
            scope,
            tool_name="read",
            paths=[mig],
            repo_root=_REPO,
        )
        assert not read_ok.violations
        write_ok = check_paths_in_scope(
            scope,
            tool_name="edit",
            paths=[mig],
            repo_root=_REPO,
        )
        assert not write_ok.violations
        env_write = check_paths_in_scope(
            scope,
            tool_name="edit",
            paths=["migrations/env.py"],
            repo_root=_REPO,
        )
        assert env_write.violations
        assert scope.restrict_write_allow == repair_product_write_globs()


def test_agent_override_restrict_allow() -> None:
    key = "__test_read_scope__"
    AGENT_READ_OVERRIDES[key] = AgentReadOverride(
        allow=("openspec/**",),
        extra_deny=("openspec/changes/secret/**",),
    )
    try:
        scope = resolve_read_scope(key)
        assert path_allowed_by_scope(
            scope, "openspec/changes/foo/design.md", repo_root=_REPO
        )
        assert not path_allowed_by_scope(scope, "src/stroy/config.py", repo_root=_REPO)
        assert not path_allowed_by_scope(
            scope, "openspec/changes/secret/design.md", repo_root=_REPO
        )
    finally:
        AGENT_READ_OVERRIDES.pop(key, None)


def test_extra_allow_does_not_bypass_global_deny() -> None:
    key = "__test_extra_allow__"
    AGENT_READ_OVERRIDES[key] = AgentReadOverride(extra_allow=("opsx/runs/**",))
    try:
        scope = resolve_read_scope(key)
        assert not path_allowed_by_scope(scope, "opsx/runs/foo/bar.log", repo_root=_REPO)
    finally:
        AGENT_READ_OVERRIDES.pop(key, None)


def test_paths_from_tool_args_read() -> None:
    paths = paths_from_tool_args("read", {"path": "src/foo.py"})
    assert paths == ["src/foo.py"]


def test_glob_pattern_not_treated_as_filesystem_path() -> None:
    paths = paths_from_tool_args(
        "glob",
        {
            "glob_pattern": "**/apply-implement/SLICE-003/**",
            "target_directory": "openspec/changes/visual",
        },
    )
    assert paths == ["openspec/changes/visual"]


def test_shell_no_substring_path_fragment() -> None:
    cmd = (
        "python -c \"print(open('src/stroy/consultation/invest_yalta/"
        "contracts.py').read())\""
    )
    paths = paths_from_shell_command(cmd)
    assert paths == []
    result = check_paths_in_scope(
        resolve_read_scope(None),
        tool_name="shell",
        paths=paths,
        repo_root=_REPO,
        command=cmd,
    )
    assert not result.violations


def test_shell_git_commit_message_not_path() -> None:
    cmd = "git commit -m 'fix a/b'"
    paths = paths_from_shell_command(cmd)
    assert "a/b" not in paths
    assert not check_paths_in_scope(
        resolve_read_scope(None),
        tool_name="shell",
        paths=paths,
        repo_root=_REPO,
        command=cmd,
    ).violations


def test_edit_tool_gated_under_restrict() -> None:
    scope = resolve_read_scope(None)
    result = check_paths_in_scope(
        scope,
        tool_name="edit",
        paths=["opsx/runs/secret.log"],
        repo_root=_REPO,
    )
    assert result.violations


def test_repo_root_grep_blocked() -> None:
    scope = resolve_read_scope(None)
    result = check_paths_in_scope(
        scope,
        tool_name="grep",
        paths=[str(_REPO)],
        repo_root=_REPO,
    )
    assert result.violations
    assert result.violations[0].rule == "repo_root_scan"


def test_shell_node_e_paths_array_literal_not_extracted_as_path() -> None:
    """``paths=[...]`` в ``node -e`` — JS literal, не путь shell."""
    cmd = (
        "node --input-type=module -e \""
        "const paths=['src/stroy/consultation/invest_yalta/handler.py'];"
        "console.log(paths);"
        '"'
    )
    paths = paths_from_shell_command(cmd)
    assert not any("paths=" in p or p.startswith("paths[") for p in paths)
    assert "src/stroy/consultation/invest_yalta/handler.py" not in paths
    scope = resolve_read_scope("apply_scaffold")
    result = check_paths_in_scope(
        scope,
        tool_name="shell",
        paths=paths,
        repo_root=_REPO,
        command=cmd,
    )
    assert not result.violations


def test_shell_node_e_bare_red_json_not_extracted_as_path() -> None:
    """Имена receipt в inline ``node -e`` не попадают в shell path list."""
    cmd = (
        'node --input-type=module -e "'
        "import { readFileSync } from 'node:fs'; "
        "readFileSync('RED.json'); "
        '"'
    )
    paths = paths_from_shell_command(cmd)
    assert "RED.json" not in paths
    assert "evil.json" not in paths_from_shell_command(
        'node -e "open(\\"evil.json\\")"'
    )
    scope = resolve_read_scope("apply_green")
    result = check_paths_in_scope(
        scope,
        tool_name="shell",
        paths=paths,
        repo_root=_REPO,
        command=cmd,
    )
    assert not result.violations


def test_apply_slice_scope_blocks_opsx_tools_read() -> None:
    scope = resolve_read_scope("apply_scaffold")
    assert scope.restrict_allow is not None
    assert not any(p.startswith("opsx_tools") for p in scope.restrict_allow)
    result = check_paths_in_scope(
        scope,
        tool_name="read",
        paths=["opsx_tools/opsx-apply-scaffold.mjs"],
        repo_root=_REPO,
    )
    assert result.violations
    assert result.violations[0].rule == "outside_restrict_allow"


def test_apply_scaffold_blocks_openspec_schemas_receipt_path() -> None:
    """Regression: visual 9468b98de2ff wandered to non-contract schema path."""
    scope = resolve_read_scope("apply_scaffold")
    result = check_paths_in_scope(
        scope,
        tool_name="read",
        paths=["openspec/schemas/opsx.apply_scaffold.json"],
        repo_root=_REPO,
    )
    assert result.violations
    assert result.violations[0].rule == "outside_restrict_allow"
    assert result.violations[0].path == "openspec/schemas/opsx.apply_scaffold.json"


def test_apply_scaffold_blocks_runtime_agent_source() -> None:
    """Regression: visual 56b43bbf4237 read apply_scaffold/agent.py for answer schema."""
    scope = resolve_read_scope("apply_scaffold")
    result = check_paths_in_scope(
        scope,
        tool_name="read",
        paths=["opsx/opsx_runtime/agents/implementation/apply_scaffold/agent.py"],
        repo_root=_REPO,
    )
    assert result.violations
    assert result.violations[0].rule == "outside_restrict_allow"
    assert (
        result.violations[0].path
        == "opsx/opsx_runtime/agents/implementation/apply_scaffold/agent.py"
    )


def test_shell_cd_repo_root_then_pytest_not_repo_root_scan() -> None:
    """``cd`` на workspace root перед ``poetry run pytest`` — не repo_root_scan."""
    scope = resolve_read_scope("apply_green")
    cmd = (
        f"cd {_REPO} && poetry run pytest -q "
        "tests/unit/channels/telegram/invest_yalta/test_photo_delivery.py"
    )
    paths = paths_from_shell_command(cmd)
    result = check_paths_in_scope(
        scope,
        tool_name="shell",
        paths=paths,
        repo_root=_REPO,
        command=cmd,
    )
    assert not any(v.rule == "repo_root_scan" for v in result.violations)


def test_shell_cd_repo_root_then_pytest_nodeids_with_stderr_redirect_not_repo_root_scan() -> None:
    """visual-13: ``cd`` + ``::`` node ids + ``2>&1`` — не repo_root_scan (не ``pytest .``)."""
    scope = resolve_read_scope("apply_red")
    cmd = (
        f"cd {_REPO} && poetry run pytest -q "
        "tests/unit/channels/telegram/invest_yalta/test_pending_confirmed_covers.py::"
        "test_pending_slot_does_not_select_confirmed_cover "
        "tests/unit/channels/telegram/invest_yalta/test_pending_confirmed_covers.py::"
        "test_pending_uses_cover_pending_received "
        "tests/unit/channels/max/invest_yalta/test_max_pending_confirmed_covers.py::"
        "test_max_pending_confirmed_cover_parity 2>&1"
    )
    paths = paths_from_shell_command(cmd)
    result = check_paths_in_scope(
        scope,
        tool_name="shell",
        paths=paths,
        repo_root=_REPO,
        command=cmd,
    )
    assert not any(v.rule == "repo_root_scan" for v in result.violations)


@pytest.mark.parametrize(
    "pytest_tail",
    (
        "poetry run pytest -q .",
        "poetry run pytest -q tests",
        "poetry run pytest -q tests/ 2>&1",
        "poetry run pytest 2>&1",
        "poetry run pytest . 2>&1",
        "poetry run pytest tests 2>&1",
    ),
)
def test_shell_cd_repo_root_then_broad_pytest_repo_root_scan(pytest_tail: str) -> None:
    """``cd`` в корень + широкий ``pytest`` (``.``/``tests``/без цели) — ``repo_root_scan``."""
    scope = resolve_read_scope("apply_green")
    cmd = f"cd {_REPO} && {pytest_tail}"
    paths = paths_from_shell_command(cmd)
    result = check_paths_in_scope(
        scope,
        tool_name="shell",
        paths=paths,
        repo_root=_REPO,
        command=cmd,
    )
    assert any(v.rule == "repo_root_scan" for v in result.violations)


def test_shell_poetry_directory_repo_root_pytest_nodeids_not_repo_root_scan() -> None:
    """``poetry --directory <repo> run pytest`` с ``::`` — cwd hint, не repo_root_scan."""
    scope = resolve_read_scope("apply_green")
    cmd = (
        'poetry --directory "C:\\Users\\vkita\\my_code\\opsx-implement" run pytest -q '
        "tests/unit/channels/max/invest_yalta/test_max_visual_media_cache.py::"
        "test_max_cover_cache_hit_skips_upload_media "
        "tests/unit/channels/max/invest_yalta/test_max_visual_media_cache.py::"
        "test_max_seven_cover_filenames_supported "
        "tests/unit/channels/max/invest_yalta/test_max_outbound_sdk_visual_cache.py::"
        "test_max_stale_token_deletes_cache_and_retries_upload"
    )
    paths = paths_from_shell_command(cmd)
    result = check_paths_in_scope(
        scope,
        tool_name="shell",
        paths=paths,
        repo_root=_REPO,
        command=cmd,
    )
    assert not any(v.rule == "repo_root_scan" for v in result.violations)


def test_shell_poetry_directory_repo_root_broad_pytest_still_repo_root_scan() -> None:
    """``poetry --directory <repo> run pytest .`` по-прежнему ``repo_root_scan``."""
    scope = resolve_read_scope("apply_green")
    cmd = f'poetry --directory "{_REPO}" run pytest -q tests'
    paths = paths_from_shell_command(cmd)
    result = check_paths_in_scope(
        scope,
        tool_name="shell",
        paths=paths,
        repo_root=_REPO,
        command=cmd,
    )
    assert any(v.rule == "repo_root_scan" for v in result.violations)


def test_shell_poetry_dash_c_repo_root_pytest_not_repo_root_scan() -> None:
    """``poetry -C <repo> run pytest`` (df981319caab) — cwd hint, не repo_root_scan."""
    scope = resolve_read_scope("apply_green")
    cmd = (
        'poetry -C "C:\\Users\\vkita\\my_code\\opsx-implement" run pytest -q '
        "tests/unit/channels/telegram/invest_yalta/test_visual_carousel_keyboard.py "
        "tests/unit/consultation/invest_yalta/test_visual_carousel_handler.py "
        "tests/unit/channels/max/invest_yalta/test_max_present.py::"
        "test_max_intro_prebook_carousel_parity_with_telegram"
    )
    paths = paths_from_shell_command(cmd)
    result = check_paths_in_scope(
        scope,
        tool_name="shell",
        paths=paths,
        repo_root=_REPO,
        command=cmd,
    )
    assert not any(v.rule == "repo_root_scan" for v in result.violations)
    checked = assert_paths_in_scope_from_record(
        scope,
        tool_name="shell",
        args={"command": cmd},
        repo_root=_REPO,
    )
    assert not checked.violations


def test_shell_git_dash_c_repo_root_status_repo_root_scan() -> None:
    """``git -C <repo_root>`` — путь корня не глобальный value-flag skip."""
    scope = resolve_read_scope("apply_green")
    cmd = f'git -C "{_REPO}" status'
    paths = paths_from_shell_command(cmd)
    assert any(isinstance(p, str) and _REPO.as_posix().lower() in p.lower() for p in paths) or any(
        str(_REPO).replace("\\", "/").lower() in p.replace("\\", "/").lower() for p in paths
    )
    result = check_paths_in_scope(
        scope,
        tool_name="shell",
        paths=paths,
        repo_root=_REPO,
        command=cmd,
    )
    assert any(v.rule == "repo_root_scan" for v in result.violations)


def test_shell_poetry_dash_c_repo_root_install_repo_root_scan() -> None:
    scope = resolve_read_scope("apply_green")
    cmd = f'poetry -C "{_REPO}" install'
    paths = paths_from_shell_command(cmd)
    result = check_paths_in_scope(
        scope,
        tool_name="shell",
        paths=paths,
        repo_root=_REPO,
        command=cmd,
    )
    assert any(v.rule == "repo_root_scan" for v in result.violations)


def test_shell_poetry_dash_c_repo_root_run_pytest_dot_repo_root_scan() -> None:
    scope = resolve_read_scope("apply_green")
    cmd = f'poetry -C "{_REPO}" run pytest -q .'
    paths = paths_from_shell_command(cmd)
    result = check_paths_in_scope(
        scope,
        tool_name="shell",
        paths=paths,
        repo_root=_REPO,
        command=cmd,
    )
    assert any(v.rule == "repo_root_scan" for v in result.violations)


def test_shell_set_location_repo_root_then_slice_pytest_not_repo_root_scan() -> None:
    """b772c1a7e4f7: Set-Location + poetry run pytest с явными ``::`` — не repo_root_scan."""
    scope = resolve_read_scope("apply_green")
    cmd = (
        f'Set-Location "{_REPO}"; poetry run pytest -q '
        "tests/unit/channels/telegram/invest_yalta/test_visual_carousel_keyboard.py::"
        "test_intro_cover_filename_maps_owner_intro_slide_sequence "
        "tests/unit/channels/telegram/invest_yalta/test_visual_carousel_keyboard.py::"
        "test_prebook_cover_filename_maps_expert_then_format "
        "tests/unit/channels/telegram/invest_yalta/test_visual_carousel_keyboard.py::"
        "test_build_intro_carousel_keyboard_exposes_dalee_and_nazad "
        "tests/unit/channels/telegram"
    )
    paths = paths_from_shell_command(cmd)
    result = check_paths_in_scope(
        scope,
        tool_name="shell",
        paths=paths,
        repo_root=_REPO,
        command=cmd,
    )
    assert not any(v.rule == "repo_root_scan" for v in result.violations)


def test_shell_set_location_repo_root_only_repo_root_scan() -> None:
    scope = resolve_read_scope("apply_green")
    cmd = f'Set-Location "{_REPO}"'
    paths = paths_from_shell_command(cmd)
    result = check_paths_in_scope(
        scope,
        tool_name="shell",
        paths=paths,
        repo_root=_REPO,
        command=cmd,
    )
    assert any(v.rule == "repo_root_scan" for v in result.violations)


def test_apply_slice_profile_is_narrower_than_global() -> None:
    global_allow = resolve_read_scope(None).restrict_allow or ()
    apply_allow = resolve_read_scope("apply_implement").restrict_allow or ()
    assert "openspec/changes/**" in apply_allow
    assert "src/**" in apply_allow
    assert not any(p.startswith(".cursor") for p in global_allow)
    assert not any(p.startswith("opsx_tools") for p in global_allow)
    assert not any(p.startswith("opsx/contracts") for p in global_allow)
    assert not any(p.startswith(".cursor") or p.startswith("opsx_tools") for p in apply_allow)
    assert resolve_read_scope("impl_verify").restrict_allow == global_allow


def test_glob_without_target_directory_is_repo_root_scan() -> None:
    scope = resolve_read_scope(None)
    with pytest.raises(ReadScopeViolation) as err:
        assert_paths_in_scope_from_record(
            scope,
            tool_name="glob",
            args={"glob_pattern": "**/x/**"},
            repo_root=_REPO,
        )
    assert err.value.rule == "repo_root_scan"


def test_grep_nested_mcp_args_with_bounded_path_not_repo_root_scan() -> None:
    """MCP обёртка custom-user-tools: ``path`` во вложенном ``args``."""
    scope = resolve_read_scope("bugbot")
    checked = assert_paths_in_scope_from_record(
        scope,
        tool_name="grep",
        args={
            "providerIdentifier": "custom-user-tools",
            "toolName": "grep",
            "args": {
                "pattern": "visual_prebook",
                "path": str(_REPO / "tests"),
                "outputMode": "content",
            },
        },
        repo_root=_REPO,
    )
    assert not any(v.rule == "repo_root_scan" for v in checked.violations)


def test_stream_sink_enforces_read_scope_on_tool_call() -> None:
    from agents.read_scope_turn import build_tool_call_guard

    scope = GLOBAL_READ_SCOPE
    guard = build_tool_call_guard(scope, _REPO)
    sink = CursorRunStreamSink(
        label="t",
        ndjson_path=None,
        emit_stderr=False,
        tool_call_guard=guard,
        repo_root=_REPO,
    )
    with pytest.raises(ReadScopeViolation):
        sink._enforce_tool_guard(
            {
                "kind": "tool_call",
                "name": "read",
                "args": {"path": "opsx/runs/x.log"},
            }
        )
    sink._enforce_tool_guard(
        {
            "kind": "tool_call",
            "name": "read",
            "args": {"path": "openspec/changes/x/spec.md"},
        }
    )


@pytest.mark.parametrize(
    ("repo", "path", "expected_rel"),
    [
        (_REPO, "openspec/changes/visual", "openspec/changes/visual"),
        (
            _REPO,
            r"C:\Users\vkita\my_code\opsx-implement\openspec\changes\visual",
            "openspec/changes/visual",
        ),
        (
            Path("/agent/repos/stroy-system"),
            "/agent/repos/stroy-system/openspec/changes/foo.md",
            "openspec/changes/foo.md",
        ),
    ],
)
def test_to_repo_relative(repo: Path, path: str, expected_rel: str) -> None:
    assert to_repo_relative(path, repo) == expected_rel


def test_path_outside_repo_denied_for_read() -> None:
    scope = resolve_read_scope(None)
    result = check_paths_in_scope(
        scope,
        tool_name="read",
        paths=["/tmp/outside.txt"],
        repo_root=Path("/agent/repos/stroy-system"),
    )
    assert result.violations
    assert result.violations[0].rule == "outside_repo"


def test_fixture_cases_jsonl() -> None:
    if not _FIXTURES.is_file():
        pytest.skip("fixtures missing")
    scope = resolve_read_scope(None)
    for line in _FIXTURES.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        tool = row["tool"]
        args = row["args"]
        expect = row["expect"]
        if expect == "ok":
            checked = assert_paths_in_scope_from_record(
                scope, tool_name=tool, args=args, repo_root=_REPO
            )
            assert not checked.violations, f"{tool} {args}"
        else:
            with pytest.raises(ReadScopeViolation):
                assert_paths_in_scope_from_record(
                    scope, tool_name=tool, args=args, repo_root=_REPO
                )


def test_read_scope_mode_default_enforce(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPSX_READ_SCOPE_MODE", raising=False)
    assert read_scope_enforcement_mode() == "enforce"
