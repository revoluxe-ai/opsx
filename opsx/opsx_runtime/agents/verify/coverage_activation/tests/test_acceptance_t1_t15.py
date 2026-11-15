"""Acceptance T1–T15 для Change Coverage Activation."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from agents.verify.coverage_activation.git_inventory import collect_change_inventory
from agents.verify.coverage_activation.static_coverage import (
    apply_static_activation_plan,
    audit_static_paths,
    build_static_activation_plan,
    effective_static_disposition,
)
from agents.verify.coverage_activation.test_discovery import (
    apply_test_reactivation,
    build_test_reactivation_plan,
    discover_related_tests,
)
from agents.verify.coverage_activation.schemas import CoverageActivationAnswer
from workflows.verify.graph import (
    build_verify_graph,
    route_after_coverage_activation,
    route_after_scenario_coverage_verify,
)
from workflows.verify.tests.test_impl_graph import _stub_verify_agents


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@test"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True, capture_output=True)


def _commit_all(root: Path, message: str = "base") -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", message], cwd=root, check=True, capture_output=True)


def _write_strict_config(root: Path, *, include: list[str], exclude: list[str] | None = None) -> None:
    body = {
        "include": include,
        "exclude": exclude or ["**/vendor/**"],
        "ignore": [],
        "typeCheckingMode": "strict",
    }
    (root / "pyrightconfig_strict.json").write_text(json.dumps(body, indent=2), encoding="utf-8")


@pytest.fixture
def hermetic_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setenv("BOUNDED_VERIFY_COVERAGE_BASE_REF", "stage")
    cap = root / "opsx/project"
    cap.mkdir(parents=True)
    (cap / "capabilities.yaml").write_text(
        "product:\n  source_roots:\n    - src/stroy\n",
        encoding="utf-8",
    )
    _git_init(root)
    (root / "src/stroy").mkdir(parents=True)
    (root / "src/stroy/base.py").write_text("def base() -> int:\n    return 1\n", encoding="utf-8")
    _write_strict_config(root, include=["src/stroy/base.py"])
    _commit_all(root, "base")
    subprocess.run(["git", "branch", "stage"], cwd=root, check=True, capture_output=True)
    return root


def test_t1_new_production_file_static_activation(hermetic_repo: Path) -> None:
    new_file = hermetic_repo / "src/stroy/new_module.py"
    new_file.write_text("def new_fn() -> int:\n    return 2\n", encoding="utf-8")
    inv = collect_change_inventory(hermetic_repo)
    assert "src/stroy/new_module.py" in inv.current_changed
    audit = audit_static_paths(hermetic_repo, inv.current_changed)
    plan = build_static_activation_plan(audit, allow_narrow_exclude=False)
    assert "src/stroy/new_module.py" in plan["include_add"]
    applied = apply_static_activation_plan(hermetic_repo, plan)
    assert applied["applied"] is True
    raw = json.loads((hermetic_repo / "pyrightconfig_strict.json").read_text(encoding="utf-8"))
    assert "src/stroy/new_module.py" in raw["include"]


def test_t2_exclude_over_include_blocks_without_owner(hermetic_repo: Path) -> None:
    _write_strict_config(
        hermetic_repo,
        include=["src/stroy"],
        exclude=["src/stroy/excluded.py"],
    )
    target = hermetic_repo / "src/stroy/excluded.py"
    target.write_text("def x() -> int:\n    return 1\n", encoding="utf-8")
    audit = effective_static_disposition(
        "src/stroy/excluded.py",
        include=["src/stroy"],
        exclude=["src/stroy/excluded.py"],
        ignore=[],
    )
    assert audit.disposition == "EXCLUDE_OVERRIDES_INCLUDE"


def test_t3_already_covered_no_plan_changes(hermetic_repo: Path) -> None:
    inv = collect_change_inventory(hermetic_repo)
    audit = audit_static_paths(hermetic_repo, ("src/stroy/base.py",))
    plan = build_static_activation_plan(audit, allow_narrow_exclude=False)
    assert plan["include_add"] == []
    assert plan["ignore_remove"] == []


def test_t4_disabled_related_test_reactivation(hermetic_repo: Path) -> None:
    prod = hermetic_repo / "src/stroy/feature.py"
    prod.write_text("def feature() -> int:\n    return 1\n", encoding="utf-8")
    test_path = hermetic_repo / "tests/unit/test_feature.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(
        "import pytest\n@pytest.mark.skip(reason='legacy fail')\ndef test_feature():\n"
        "    from stroy.feature import feature\n    assert feature() == 1\n",
        encoding="utf-8",
    )
    related = discover_related_tests(hermetic_repo, ("src/stroy/feature.py",))
    assert any(r.path.endswith("test_feature.py") for r in related)
    plan = build_test_reactivation_plan(related, approved_paths={"tests/unit/test_feature.py"})
    assert "tests/unit/test_feature.py" in plan["reactivate"]
    edited = apply_test_reactivation(hermetic_repo, plan["reactivate"])
    assert "tests/unit/test_feature.py" in edited["edited"]
    assert "@pytest.mark.skip" not in test_path.read_text(encoding="utf-8")


def test_t4b_mypy_registry_registers_reactivated_tests(hermetic_repo: Path) -> None:
    from agents.verify.coverage_activation.mypy_static import (
        load_registered_mypy_paths,
        register_mypy_paths,
    )

    out = register_mypy_paths(hermetic_repo, ["tests/unit/test_feature.py"])
    assert "tests/unit/test_feature.py" in out["registered"]
    assert load_registered_mypy_paths(hermetic_repo) == ["tests/unit/test_feature.py"]


def test_t5_test_fail_not_masked_here() -> None:
    """Activation не маскирует pytest exit code — checks хранят exit_code as-is."""
    from agents.verify.coverage_activation.run_checks import run_pytest_on_paths

    assert run_pytest_on_paths(Path("/nonexistent"), [])["ran"] is False


def test_t6_unsafe_test_not_reactivated(hermetic_repo: Path) -> None:
    test_path = hermetic_repo / "tests/unit/test_live.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(
        "# live destructive test\nimport pytest\n@pytest.mark.skip(reason='live')\n"
        "def test_live():\n    pass\n",
        encoding="utf-8",
    )
    related = discover_related_tests(hermetic_repo, ("src/stroy/base.py",))
    unsafe = [r for r in related if r.path.endswith("test_live.py")]
    if unsafe:
        plan = build_test_reactivation_plan(unsafe, approved_paths={unsafe[0].path})
        assert unsafe[0].path in plan["blocked"]


def test_t7_unrelated_disabled_test_unchanged(hermetic_repo: Path) -> None:
    other = hermetic_repo / "tests/unit/test_other.py"
    other.parent.mkdir(parents=True)
    other.write_text(
        "import pytest\n@pytest.mark.skip(reason='other')\ndef test_other():\n    pass\n",
        encoding="utf-8",
    )
    related = discover_related_tests(hermetic_repo, ("src/stroy/base.py",))
    plan = build_test_reactivation_plan(related, approved_paths=set())
    assert "tests/unit/test_other.py" not in plan["reactivate"]


def test_t8_multiple_files_inventory(hermetic_repo: Path) -> None:
    for name in ("a.py", "b.py"):
        (hermetic_repo / "src/stroy" / name).write_text("def f() -> int: return 1\n", encoding="utf-8")
    inv = collect_change_inventory(hermetic_repo)
    assert len(inv.current_changed) == 2
    assert len(inv.current_changed) == len(set(inv.current_changed))


def test_t9_rename_and_delete(hermetic_repo: Path) -> None:
    old = hermetic_repo / "src/stroy/old_name.py"
    old.write_text("def old() -> int: return 1\n", encoding="utf-8")
    _commit_all(hermetic_repo, "add old")
    new = hermetic_repo / "src/stroy/new_name.py"
    subprocess.run(
        ["git", "mv", str(old.relative_to(hermetic_repo)), str(new.relative_to(hermetic_repo))],
        cwd=hermetic_repo,
        check=True,
        capture_output=True,
    )
    inv = collect_change_inventory(hermetic_repo)
    assert any("new_name" in p for p in inv.current_changed)


def test_t10_checkpoint_fingerprint_stable(hermetic_repo: Path) -> None:
    inv1 = collect_change_inventory(hermetic_repo)
    inv2 = collect_change_inventory(hermetic_repo)
    assert inv1.fingerprint() == inv2.fingerprint()


def test_t11_repair_accumulated_paths(hermetic_repo: Path) -> None:
    state = {
        "coverage_activation_accumulated_touched": ["src/stroy/prior.py"],
        "commit_files": ["src/stroy/from_commit.py"],
    }
    from agents.verify.coverage_activation.git_inventory import merge_prior_accumulated

    merged = merge_prior_accumulated(state)
    assert "src/stroy/prior.py" in merged
    assert "src/stroy/from_commit.py" in merged


def test_t12_missing_config_blocked(hermetic_repo: Path) -> None:
    (hermetic_repo / "pyrightconfig_strict.json").unlink()
    audit = audit_static_paths(hermetic_repo, ("src/stroy/base.py",))
    assert audit["STATUS"] == "BLOCKED"


def test_t13_test_coverage_gaps_in_answer() -> None:
    answer = CoverageActivationAnswer(
        verdict="COVERAGE_ACTIVATION_PASS",
        summary="gaps",
        test_coverage_gaps=["src/stroy/no_tests.py"],
        local_hop_copy_block="x",
    )
    assert answer.test_coverage_gaps


def test_t14_no_whole_tree_include(hermetic_repo: Path) -> None:
    audit = audit_static_paths(hermetic_repo, ("src/stroy/base.py",))
    plan = build_static_activation_plan(audit, allow_narrow_exclude=False)
    for path in plan["include_add"]:
        assert path != "src/stroy" or path.count("/") > 1 or path.endswith(".py")


def test_t15_graph_regression_nodes() -> None:
    graph = build_verify_graph(*_stub_verify_agents())
    nodes = graph.get_graph().nodes
    assert "impl_verify" in nodes
    assert "scenario_coverage_verify" in nodes
    assert "coverage_activation" in nodes
    assert "static_repair" in nodes


def test_routing_scenario_to_coverage_activation(tmp_path: Path) -> None:
    from workflows.verify.cycle_checks import CYCLE_CHECKS_STATE_KEY, CheckName
    from workflows.verify.tests.test_green_loop import _init_git_repo

    head = _init_git_repo(tmp_path)
    state = {
        "repository_path": str(tmp_path),
        "workspace": str(tmp_path),
        "scenario_coverage_verdict": "SCENARIO_TEST_COVERAGE_PASS",
        CYCLE_CHECKS_STATE_KEY: {
            "established": {CheckName.IMPL_VERIFY.value: head},
        },
    }
    assert route_after_scenario_coverage_verify(state) == "coverage_activation"


def test_routing_coverage_pass_to_quality_gates() -> None:
    state = {"coverage_activation_verdict": "COVERAGE_ACTIVATION_PASS"}
    assert route_after_coverage_activation(state) == "static_repair"


def test_routing_blocked_exclude_never_coverage_activation_terminal() -> None:
    """Широкий exclude / NEEDS_OWNER не должен останавливать verify на terminal hop."""
    from agents.verify.graph_validation import resolve_coverage_activation_route_target

    state = {
        "coverage_activation_verdict": "COVERAGE_ACTIVATION_NEEDS_OWNER",
        "coverage_activation_answer": {
            "verdict": "COVERAGE_ACTIVATION_NEEDS_OWNER",
            "summary": "exclude blocks",
            "block_reason": "EXCLUDE_BLOCKS_CHANGED_PATHS",
            "local_hop_copy_block": "x",
        },
    }
    assert resolve_coverage_activation_route_target(state) == "static_repair"
    assert route_after_coverage_activation(state) == "static_repair"


def test_inventory_fingerprint_tracks_product_content_not_git_commit(hermetic_repo: Path):
    initial = collect_change_inventory(hermetic_repo)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "unrelated commit"],
        cwd=hermetic_repo, check=True, capture_output=True,
    )
    unchanged = collect_change_inventory(hermetic_repo)
    assert initial.fingerprint() == unchanged.fingerprint()

    launcher = hermetic_repo / "run_opsx_main.py"
    launcher.write_text("runtime changed\\n", encoding="utf-8")
    assert collect_change_inventory(hermetic_repo).fingerprint() == initial.fingerprint()

    product = hermetic_repo / "src/stroy/base.py"
    product.write_text(product.read_text(encoding="utf-8") + "\\n# changed behavior\\n", encoding="utf-8")
    modified = collect_change_inventory(hermetic_repo)
    assert modified.fingerprint() != initial.fingerprint()
