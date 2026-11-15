# -*- coding: utf-8 -*-
"""P0-C: registry executor must not false-green on export-only / placeholders."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BV_ROOT = REPO_ROOT / ".cursor/pipeline/bounded-verify"
sys.path.insert(0, str(BV_ROOT))

from bounded_verify_executor import BoundedVerifyExecutorError, run_current_check  # noqa: E402
from bounded_verify_registry_execution import (  # noqa: E402
    RegistryExecutionSupport,
    registry_check_support_matrix,
    resolve_registry_execution_contract,
)


def test_destructive_export_only_unsupported() -> None:
    contract = resolve_registry_execution_contract(
        "pytest-destructive",
        REPO_ROOT / ".cursor/static-check/registry.yaml",
        root=REPO_ROOT,
    )
    assert contract.support == RegistryExecutionSupport.UNSUPPORTED


def test_targeted_placeholders_unsupported() -> None:
    contract = resolve_registry_execution_contract(
        "pytest-targeted",
        REPO_ROOT / ".cursor/static-check/registry.yaml",
        root=REPO_ROOT,
    )
    assert contract.support == RegistryExecutionSupport.UNSUPPORTED


def test_executor_blocks_destructive_check() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        try:
            from bounded_verify_executor import _default_check_runner

            _default_check_runner(
                "pytest-destructive",
                REPO_ROOT / ".cursor/static-check/registry.yaml",
                root,
            )
        except BoundedVerifyExecutorError as exc:
            assert exc.code == "EXECUTOR_BLOCKED"
        else:
            raise AssertionError("destructive must not PASS via export-only")


def test_support_matrix() -> None:
    matrix = registry_check_support_matrix(root=REPO_ROOT)
    assert matrix["pyright-strict"] == RegistryExecutionSupport.MACHINE_EXECUTABLE.value
    assert matrix["pytest-destructive"] == RegistryExecutionSupport.UNSUPPORTED.value
    assert matrix["pytest-targeted"] == RegistryExecutionSupport.UNSUPPORTED.value


def test_mypy_pack_true_mutation_blocked(tmp_path: Path) -> None:
    import shutil

    reg_src = REPO_ROOT / ".cursor/static-check/registry.yaml"
    pack = tmp_path / ".cursor/static-check/checks/mypy"
    pack.mkdir(parents=True)
    (pack / "instructions.md").write_text(
        "```bash\ntrue\n```\n",
        encoding="utf-8",
    )
    reg = tmp_path / "registry.yaml"
    shutil.copy2(reg_src, reg)
    contract = resolve_registry_execution_contract("mypy", reg, root=tmp_path)
    assert contract.support == RegistryExecutionSupport.BLOCKED


def test_pyright_pack_echo_mutation_blocked(tmp_path: Path) -> None:
    import shutil

    reg_src = REPO_ROOT / ".cursor/static-check/registry.yaml"
    pack = tmp_path / ".cursor/static-check/checks/pyright-strict"
    pack.mkdir(parents=True)
    (pack / "instructions.md").write_text(
        "```bash\necho ok\n```\n",
        encoding="utf-8",
    )
    reg = tmp_path / "registry.yaml"
    shutil.copy2(reg_src, reg)
    contract = resolve_registry_execution_contract(
        "pyright-strict", reg, root=tmp_path
    )
    assert contract.support == RegistryExecutionSupport.BLOCKED


def main() -> int:
    test_destructive_export_only_unsupported()
    test_targeted_placeholders_unsupported()
    test_executor_blocks_destructive_check()
    test_support_matrix()
    print("registry executor adversarial OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
