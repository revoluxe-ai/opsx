"""Focused fail-closed tests for manual /opsx-run-build helper."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any


HELPER = Path(__file__).resolve().parents[1] / "run-build" / "run_build.py"
ARTIFACT_SUBDIR = ".cursor/pipeline/run-build/artifacts/ci-test"


def _load_module() -> Any:
    """Import the run_build helper from its pipeline path."""

    spec = importlib.util.spec_from_file_location("run_build_helper", HELPER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load helper from {HELPER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_build_helper"] = module
    spec.loader.exec_module(module)
    return module


def _minimal_poetry_project(tmp_path: Path) -> Path:
    """Create a tiny repo root with a Poetry build backend declaration."""

    root = tmp_path / "repo"
    root.mkdir()
    _ = (root / "pyproject.toml").write_text(
        "[tool.poetry]\nname = 'demo'\nversion = '0.1.0'\n\n"
        "[build-system]\nrequires = ['poetry-core>=1.0.0']\n"
        "build-backend = 'poetry.core.masonry.api'\n",
        encoding="utf-8",
    )
    return root


def test_bounded_mode_binding_gap() -> None:
    """Refuse frozen S6 bounded builds until capability is registered."""

    module = _load_module()
    result = module.evaluate_manual_build(
        Path("."),
        "mode=bounded change=demo capability_id=package_build",
    )
    assert result.status == "BLOCKED"
    assert result.reason == "BINDING_GAP"
    assert "S6_PACKAGE_BUILD_CAPABILITY_UNREGISTERED" in result.detail
    assert result.runner_invoked is False


def test_missing_output_dir_blocked(tmp_path: Path) -> None:
    """Require an explicit owned output_dir for poetry_package."""

    module = _load_module()
    root = _minimal_poetry_project(tmp_path)
    result = module.evaluate_manual_build(root, "build_recipe=poetry_package")
    assert result.status == "BLOCKED"
    assert result.reason == "OUTPUT_DIR_REQUIRED"
    assert result.runner_invoked is False


def test_npm_recipe_adapter_gap(tmp_path: Path) -> None:
    """Reject SOURCE vue-npm recipe on poetry-python target."""

    module = _load_module()
    root = _minimal_poetry_project(tmp_path)
    result = module.evaluate_manual_build(
        root,
        f"build_recipe=npm_run_build output_dir={ARTIFACT_SUBDIR}",
    )
    assert result.status == "BLOCKED"
    assert result.reason == "EXECUTOR_ADAPTER_GAP"
    assert result.runner_invoked is False


def test_output_dir_not_owned_blocked(tmp_path: Path) -> None:
    """Reject writes outside the owned artifacts prefix (e.g. dist/)."""

    module = _load_module()
    root = _minimal_poetry_project(tmp_path)
    result = module.evaluate_manual_build(
        root,
        "build_recipe=poetry_package output_dir=dist/manual",
    )
    assert result.status == "BLOCKED"
    assert result.reason == "OUTPUT_DIR_NOT_OWNED"
    assert result.runner_invoked is False


def test_path_traversal_blocked(tmp_path: Path) -> None:
    """Reject parent-segment output_dir before resolving paths."""

    module = _load_module()
    root = _minimal_poetry_project(tmp_path)
    result = module.evaluate_manual_build(
        root,
        "build_recipe=poetry_package output_dir=.cursor/pipeline/run-build/artifacts/../secrets",
    )
    assert result.status == "BLOCKED"
    assert result.reason == "PATH_TRAVERSAL"


def test_mock_runner_build_failed(tmp_path: Path) -> None:
    """Surface non-zero Poetry exit as FAIL without claiming verification PASS."""

    module = _load_module()
    root = _minimal_poetry_project(tmp_path)

    def fail_runner(_argv: object, _cwd: Path) -> int:
        return 1

    result = module.evaluate_manual_build(
        root,
        f"build_recipe=poetry_package output_dir={ARTIFACT_SUBDIR}/fail",
        runner=fail_runner,
    )
    assert result.status == "FAIL"
    assert result.reason == "BUILD_FAILED"
    assert result.exit_code == 1
    assert result.resolved_command[0:2] == ("poetry", "build")


def test_mock_runner_pass_resolves_argv(tmp_path: Path) -> None:
    """Prove safe argv shape when output_dir is owned and backend exists."""

    module = _load_module()
    root = _minimal_poetry_project(tmp_path)
    seen: list[tuple[str, ...]] = []

    def ok_runner(argv: object, _cwd: Path) -> int:
        seen.append(tuple(argv))  # type: ignore[arg-type]
        return 0

    result = module.evaluate_manual_build(
        root,
        f"build_recipe=poetry_package output_dir={ARTIFACT_SUBDIR}/dry",
        runner=ok_runner,
    )
    assert result.status == "PASS"
    assert result.exit_code == 0
    assert seen
    assert seen[0][0:2] == ("poetry", "build")
    assert "--output" in seen[0]
    assert seen[0][-1] == "--no-interaction"
