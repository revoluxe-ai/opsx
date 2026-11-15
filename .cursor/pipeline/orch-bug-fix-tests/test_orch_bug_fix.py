"""Fail-closed and delegation tests for orch-bug-fix."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
_ADOPTION_CONFTEST = (
    REPO_ROOT / ".cursor/pipeline/engineering-adoption-tests/conftest.py"
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_adoption_conftest():
    spec = importlib.util.spec_from_file_location(
        "engineering_adoption_conftest",
        _ADOPTION_CONFTEST,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_adoption = _load_adoption_conftest()
write_minimal_projectization = _adoption.write_minimal_projectization
write_verified_adoption = _adoption.write_verified_adoption

HELPER = Path(__file__).resolve().parents[1] / "orch-bug-fix" / "orch_bug_fix.py"


def _load():
    spec = importlib.util.spec_from_file_location("orch_bug_fix", HELPER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _init(root: Path) -> None:
    write_minimal_projectization(root)
    write_verified_adoption(root)


def test_process_findings_do_not_clear_product_batch(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _init(root)
    result = _load().run(
        root,
        {
            "findings": [
                {
                    "location": ".cursor/commands/opsx-commit.md:10",
                    "finding": "process defect",
                }
            ]
        },
    )
    assert result["STATUS"] == "PARTIAL_COMPLETE"
    assert result["BUG_BATCH_CLEAR"] == "NO"
    assert result["PROCESS_HANDOFF"] == "YES"
    assert result["NEXT_COMMAND"] == "/opsx-meta-fix-command-defects"
    assert result["TEMPORAL_NESTED_START"] == "NO"


def test_family_plan_go_native_persist(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _init(root)
    from bug_lifecycle.orch_mission_runtime import advance

    base = {
        "mission": "ORCH-unit-family",
        "execution_id": "exec-1",
        "head": "a" * 40,
        "change": "bot-guard",
        "findings": [{"location": "app/bot.py:1", "finding": "F1"}],
    }
    _ = advance(root, base)
    result = _load().run(
        root,
        {**base, "auto_persist_command": "/opsx-bug-family-plan GO"},
    )
    assert result["PERSIST_MODE"] == "NATIVE"
    assert result["STATUS"] in {"PERSISTED", "STAGE_ADVANCED"}
    assert (root / "opsx/runs/orch-missions/ORCH-unit-family/family-plan.json").is_file()


def test_disposition_go_native_persist(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _init(root)
    from bug_lifecycle.orch_mission_runtime import advance

    mission = "ORCH-unit-disp"
    base = {
        "mission": mission,
        "execution_id": "exec-1",
        "head": "b" * 40,
        "change": "bot-guard",
        "findings": [{"location": "app/bot.py:1", "finding": "F1"}],
    }
    _ = advance(root, base)
    _ = _load().run(root, {**base, "auto_persist_command": "/opsx-bug-family-plan GO"})
    result = _load().run(root, {**base, "auto_persist_command": "/opsx-bug-disposition GO"})
    assert result["PERSIST_MODE"] == "NATIVE"
    assert (root / "opsx/runs/bug-cases").is_dir()


def test_batch_clear_requires_materialize_receipt_identity(orch_hermetic_root: Path) -> None:
    root = orch_hermetic_root
    _init(root)
    module = _load()
    from bug_lifecycle.test_request import with_hermetic_executor

    request = with_hermetic_executor(
        {
            "mission": "ORCH-unit-clear",
            "change": "bot-guard",
            "execution_id": "exec-1",
            "head": "c" * 40,
            "native_mission": "YES",
            "findings": [{"location": "app/bot.py:1", "finding": "F1"}],
        }
    )
    cleared = module.run(root, request)
    assert cleared["BUG_BATCH_CLEAR"] == "YES"
    forged = module.run(
        root,
        {
            **request,
            "mission": "ORCH-forged-close",
            "close_evidence": {"execution_id": "exec-1", "closed_ids": ["F1"]},
            "native_mission": "",
            "repaired": "YES",
        },
    )
    assert forged.get("BUG_BATCH_CLEAR") != "YES"


def test_mixed_batch_continues_product_pp(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _init(root)
    result = _load().run(
        root,
        {
            "change": "bot-guard",
            "findings": [
                {"location": ".cursor/commands/x.md:1", "finding": "P1"},
                {"location": "app/bot.py:1", "finding": "F1"},
            ],
        },
    )
    assert result["STATUS"] == "REPAIR_DISPATCH"
    assert result["PROCESS_HANDOFF"] == "YES"
    assert result["MIXED_BATCH_POLICY"] == "PRODUCT_PP_CONTINUES"


def test_materialize_go_native_persist(orch_hermetic_root: Path) -> None:
    root = orch_hermetic_root
    _init(root)
    mission = "ORCH-unit-mat"
    module = _load()
    from bug_lifecycle.test_request import with_hermetic_executor

    prep = with_hermetic_executor(
        {
            "mission": mission,
            "execution_id": "exec-1",
            "head": "d" * 40,
            "change": "bot-guard",
            "findings": [{"location": "app/bot.py:1", "finding": "F1"}],
            "native_mission": "YES",
        }
    )
    cleared = module.run(root, prep)
    assert cleared["BUG_BATCH_CLEAR"] == "YES"


def test_second_transport_forbidden(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _init(root)
    result = _load().run(root, {"transport": "TASK_BACKGROUND"})
    assert result["REASON"] == "SECOND_ASYNC_TRANSPORT_FORBIDDEN"


def test_stale_mission_blocked(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _init(root)
    module = _load()
    first = module.run(
        root,
        {
            "mission": "ORCH-bug-fix-1",
            "change": "bot-guard",
            "head": "a" * 40,
            "execution_id": "exec-1",
            "findings": [{"location": "app/bot.py:1", "finding": "F1"}],
        },
    )
    assert first["STATUS"] == "REPAIR_DISPATCH"
    stale = module.run(
        root,
        {
            "mission": "ORCH-bug-fix-1",
            "change": "bot-guard",
            "head": "b" * 40,
            "execution_id": "exec-1",
            "findings": [{"location": "app/bot.py:1", "finding": "F1"}],
        },
    )
    assert stale["REASON"] == "stale_mission"
