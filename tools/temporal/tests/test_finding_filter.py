import pytest

from finding_filter import (
    PROCESS_LOCATION_PREFIXES,
    PRODUCT_LOCATION_PREFIXES,
    is_repair_excluded_location,
    partition_repair_findings,
)


@pytest.mark.parametrize(
    "location",
    [
        "opsx/tools/lib/opsx-runtime-executor.mjs:167",
        "`opsx/tools/lib/opsx-runtime-kernel.mjs:945`",
        "opsx/upstream/a71601dba3b9/.cursor/commands/opsx-bug-team.md:1",
        "opsx-knowledge/KNOWLEDGE.jsonl:10",
        ".cursor/commands/opsx-orch-bug-fix.md:252",
        ".cursor/rules/testing-rules.mdc:1",
        ".cursor/skills/foo/SKILL.md:1",
        ".cursor/agents/bar.md:1",
        ".cursor/pipeline/runtime-core/V0_ARCHITECTURE.md:100",
        ".cursor/static-check/foo.mjs:1",
        ".agents/custom-agent.md:1",
        "opsx/runs/orch-missions/ORCH-bug-fix.json:1",
        "opsx/runs/bug-cases/findings/case.md:1",
        ".tmp/opsx-runtime-smoke-dirty.txt:1",
        "tools/temporal/git_preflight.py:110",
        "tools/temporal/loop_engine.py:30",
        "tools/temporal/workflow.py:1",
        "forum/engine/foo.js:1",
        "package.json:1",
    ],
)
def test_non_product_locations_are_excluded(location: str):
    assert is_repair_excluded_location(location)


@pytest.mark.parametrize(
    "location",
    [
        "src/store/index.js:464",
        "src/utils/cloudProgressHydrateCoordinator.js:16",
        "tests/unit/bankAuthDailyBonusStreakFamily.test.mjs:10",
        "backend/app/progress.py:42",
        "openspec/changes/cross-device-wardrobe-refresh/tasks.md:3",
    ],
)
def test_product_lane_locations_are_repairable(location: str):
    assert not is_repair_excluded_location(location)


def test_allowlist_is_game_tree_only():
    assert PRODUCT_LOCATION_PREFIXES == ("src/", "tests/", "backend/", "openspec/changes/")
    assert "tools/temporal/" in PROCESS_LOCATION_PREFIXES
    assert "tools/" in PROCESS_LOCATION_PREFIXES


def test_documented_prefixes_are_covered_by_process_rules():
    documented = {
        ".cursor/commands/",
        ".cursor/rules/",
        ".cursor/skills/",
        ".cursor/agents/",
        ".cursor/pipeline/",
        ".cursor/static-check/",
    }
    for prefix in documented:
        sample = f"{prefix}sample.txt:1"
        assert is_repair_excluded_location(sample), sample
    assert ".cursor/" in PROCESS_LOCATION_PREFIXES


def test_mixed_findings_partition():
    rows = [
        {"severity": "High", "location": "src/a.js:1", "finding": "product"},
        {"severity": "High", "location": "opsx/tools/a.mjs:1", "finding": "process"},
        {"severity": "Medium", "location": ".cursor/rules/x.mdc:2", "finding": "process"},
        {"severity": "High", "location": "tools/temporal/git_preflight.py:110", "finding": "poc"},
    ]
    repairable, excluded = partition_repair_findings(rows)
    assert len(repairable) == 1
    assert repairable[0]["location"] == "src/a.js:1"
    assert len(excluded) == 3
