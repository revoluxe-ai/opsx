from pathlib import Path

import pytest

from model_config import AgentModelConfig, parse_tier_pair, write_step_overlay
from tempo_operator import (
    build_start_cmd,
    get_pipeline,
    parse_bugbot_cli_tokens,
    parse_project_bug_hunt_cli_tokens,
    parse_security_cli_tokens,
    resolve_bugbot_args,
    resolve_project_bug_hunt_args,
    resolve_security_args,
)


def test_default_model_mapping():
    cfg = AgentModelConfig.load()
    expected = {
        "low": ("Composer 2.5", "composer-2.5", "composer-2.5", "low"),
        "medium": (
            "GPT-5.6 Luna Extra High",
            "gpt-5.6-luna-xhigh",
            "composer-2.5",
            "high",
        ),
        "high": (
            "Cursor Grok 4.6 Medium",
            "cursor-grok-4.6-medium",
            "composer-2.5",
            "medium",
        ),
    }
    for tier_name, (label, model, cli_model, reasoning) in expected.items():
        tier = cfg.get_tier(tier_name)
        assert tier.label == label
        assert tier.model == model
        assert tier.cli_model == cli_model
        assert tier.reasoning == reasoning


def test_step_mapping_from_json():
    cfg = AgentModelConfig.load()
    assert cfg.resolve_step("review").cli_model == "composer-2.5"
    assert cfg.resolve_step("repair").cli_model == "composer-2.5"
    assert cfg.resolve_step("next_check").cli_model == "composer-2.5"
    assert cfg.resolve_step("verification_executor").cli_model == "composer-2.5"


def test_overlay_low_low(tmp_path: Path):
    dest = tmp_path / "overlay.json"
    write_step_overlay(review="low", repair="low", dest=dest)
    overlay = AgentModelConfig.load(dest)
    assert overlay.resolve_step("review").cli_model == "composer-2.5"
    assert overlay.resolve_step("repair").cli_model == "composer-2.5"


def test_parse_tier_pair():
    assert parse_tier_pair("LOW", "medium") == ("low", "medium")


@pytest.mark.parametrize("review", ["low", "medium", "high"])
def test_operator_review_repair_contract_stays_two_positional_tiers(review: str):
    assert parse_tier_pair(review, "low") == (review, "low")


def test_project_bug_hunt_pipeline_spec():
    spec = get_pipeline("project-bug-hunt")
    assert spec["command"] == "tempo-project-bug-hunt"
    assert spec["review_activity"] == "execute_project_bug_hunt"
    assert spec["repair_activity"] == "execute_project_bug_hunt_orch"
    assert spec["clean_status"] == "HUNT_CLEAN"
    assert "HUNT_CLEAN" in spec["ok_statuses"]
    assert spec["requires"] == ["change"]
    assert spec["resolves"]["head"]["source"] == "git_current_branch"
    assert spec["defaults"]["base"] == "stage"


def test_parse_project_bug_hunt_tokens():
    parsed = parse_project_bug_hunt_cli_tokens(
        ["change=my-change", "base=main", "low", "medium"]
    )
    assert parsed == {
        "review": "low",
        "repair": "medium",
        "change": "my-change",
        "base": "main",
    }


def test_parse_rejects_head_override():
    with pytest.raises(ValueError, match="head is always"):
        parse_project_bug_hunt_cli_tokens(["change=x", "head=foo", "low", "low"])


def test_resolve_project_bug_hunt_args(tmp_path: Path):
    subprocess_init = tmp_path / ".git"
    subprocess_init.mkdir()
    # minimal git repo for branch resolution
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "checkout", "-b", "feature-branch"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    resolved = resolve_project_bug_hunt_args(
        tmp_path, change="my-change", base="stage"
    )
    assert resolved["change"] == "my-change"
    assert resolved["base"] == "stage"
    assert resolved["head"] == "feature-branch"


def test_speed_fast_variant():
    data = {
        "version": 1,
        "default_tier": "low",
        "tiers": {
            "low": {
                "model": "composer-2.5",
                "reasoning": "low",
                "speed": "fast",
            }
        },
        "speed_variants": {"composer-2.5": "composer-2.5-fast"},
    }
    fast_cfg = AgentModelConfig(data)
    assert fast_cfg.get_tier("low").cli_model == "composer-2.5-fast"


def test_bugbot_pipeline_spec():
    spec = get_pipeline("bugbot")
    assert spec["command"] == "tempo-bugbot"
    assert spec["lane"] == "bugbot"
    assert spec["review_activity"] == "execute_bugbot_review"
    assert spec["repair_activity"] == "execute_opsx_repair"
    assert spec["clean_status"] == "BUGBOT_CLEAN"
    assert "BUGBOT_CLEAN" in spec["ok_statuses"]
    assert spec["requires"] == []
    assert spec["git_preflight"] is True
    assert spec["resolves"]["head"]["source"] == "git_current_branch"
    assert spec["defaults"]["base"] == "stage"


def test_parse_bugbot_tokens():
    parsed = parse_bugbot_cli_tokens(["low", "low"])
    assert parsed == {"review": "low", "repair": "low", "base": None}

    parsed = parse_bugbot_cli_tokens(["base=main", "medium", "low"])
    assert parsed == {"review": "medium", "repair": "low", "base": "main"}


def test_parse_bugbot_rejects_change_and_head():
    with pytest.raises(ValueError, match="does not accept change="):
        parse_bugbot_cli_tokens(["change=x", "low", "low"])
    with pytest.raises(ValueError, match="head is always"):
        parse_bugbot_cli_tokens(["head=foo", "low", "low"])


def test_resolve_bugbot_args(tmp_path: Path):
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "checkout", "-b", "bugbot-branch"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    resolved = resolve_bugbot_args(tmp_path, base="stage")
    assert resolved["base"] == "stage"
    assert resolved["head"] == "bugbot-branch"


def test_build_start_cmd_bugbot_no_change(tmp_path: Path):
    spec = get_pipeline("bugbot")
    overlay = tmp_path / "overlay.json"
    overlay.write_text("{}", encoding="utf-8")
    cmd = build_start_cmd(
        spec,
        repo=tmp_path,
        overlay=overlay,
        review="low",
        repair="low",
        resolved={"base": "stage", "head": "feature"},
    )
    assert "--lane" in cmd
    lane_idx = cmd.index("--lane")
    assert cmd[lane_idx + 1] == "bugbot"
    assert "--change" not in cmd
    assert "--base" in cmd and cmd[cmd.index("--base") + 1] == "stage"
    assert "--head" in cmd and cmd[cmd.index("--head") + 1] == "feature"


def test_security_pipeline_spec():
    spec = get_pipeline("security")
    assert spec["command"] == "tempo-security"
    assert spec["lane"] == "security"
    assert spec["review_activity"] == "execute_security_review"
    assert spec["repair_activity"] == "execute_security_repair"
    assert spec["clean_status"] == "SECURITY_CLEAN"
    assert "SECURITY_CLEAN" in spec["ok_statuses"]
    assert spec["inner_repair_command"] == "/opsx-bug-team"
    assert spec["requires"] == []
    assert spec["git_preflight"] is True
    assert spec["defaults"]["base"] == "stage"


def test_parse_security_tokens():
    parsed = parse_security_cli_tokens(["low", "low"])
    assert parsed == {"review": "low", "repair": "low", "base": None}

    parsed = parse_security_cli_tokens(["base=main", "medium", "low"])
    assert parsed == {"review": "medium", "repair": "low", "base": "main"}


def test_parse_security_rejects_change_and_head():
    with pytest.raises(ValueError, match="does not accept change="):
        parse_security_cli_tokens(["change=x", "low", "low"])
    with pytest.raises(ValueError, match="head is always"):
        parse_security_cli_tokens(["head=foo", "low", "low"])


def test_resolve_security_args(tmp_path: Path):
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "checkout", "-b", "security-branch"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    resolved = resolve_security_args(tmp_path, base="stage")
    assert resolved["base"] == "stage"
    assert resolved["head"] == "security-branch"


def test_build_start_cmd_security_no_change(tmp_path: Path):
    spec = get_pipeline("security")
    overlay = tmp_path / "overlay.json"
    overlay.write_text("{}", encoding="utf-8")
    cmd = build_start_cmd(
        spec,
        repo=tmp_path,
        overlay=overlay,
        review="low",
        repair="low",
        resolved={"base": "stage", "head": "feature"},
    )
    assert cmd[cmd.index("--lane") + 1] == "security"
    assert "--change" not in cmd
    assert "--base" in cmd and cmd[cmd.index("--base") + 1] == "stage"
