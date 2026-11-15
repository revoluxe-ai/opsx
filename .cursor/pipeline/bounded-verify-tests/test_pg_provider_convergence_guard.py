# -*- coding: utf-8 -*-
"""PG provider gate: drainable BLOCKED FAIL stops Temporal as owner-blocked."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BV_ROOT = REPO_ROOT / ".cursor/pipeline/bounded-verify"
sys.path.insert(0, str(BV_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tools/temporal"))


def _seed(root: Path, change: str) -> None:
    sys.path.insert(0, str(BV_ROOT.parent / "bounded-verify-tests"))
    from test_impl_seed import seed_hermetic_impl_ok

    for rel in (
        ".cursor/static-check/registry.yaml",
        "openspec/policies/VERIFY_POLICY.md",
        ".cursor/pipeline/bounded-verify/verification_obligations.py",
        "opsx/project/capabilities.yaml",
    ):
        src = REPO_ROOT / rel
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    cdir = root / "openspec/changes" / change
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "tasks.md").write_text("- [x] done\n", encoding="utf-8")
    (cdir / "proposal.md").write_text("# p\n", encoding="utf-8")
    for rel in ("src/stroy", "tests/unit", "tests/integration"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    seed_hermetic_impl_ok(change, root=root)


def test_pg_provider_blocked_drains_with_last_result(tmp_path: Path) -> None:
    venv_bin = REPO_ROOT / ".venv" / "bin"
    if venv_bin.is_dir():
        os.environ["PATH"] = f"{venv_bin}{os.pathsep}{os.environ.get('PATH', '')}"
    from bounded_verify_executor import run_current_check
    from bounded_verify_ledger import read_ledger, schedule_write
    from bounded_verify_plan import draft_plan, freeze_plan
    from verification_engine import TERMINAL_BLOCKED_CHECK, classify_next_check

    change = "pg-provider-guard"
    _seed(tmp_path, change)
    reg = tmp_path / ".cursor/static-check/registry.yaml"
    checks = [{"check_id": "opsx-pg-provider-receipt", "order": 1}]
    draft_plan(change, checks, root=tmp_path, registry_path=reg)
    freeze_plan(change, root=tmp_path, registry_path=reg)
    schedule_write(change, root=tmp_path)

    def _blocked_runner(cid: str, _reg: Path, _root: Path) -> tuple[str, dict[str, str | int]]:
        return "FAIL", {"CHECK_ID": cid, "STATUS": "BLOCKED", "EXIT_CODE": 3}

    run_current_check(
        change,
        root=tmp_path,
        registry_path=reg,
        check_runner=_blocked_runner,
    )
    schedule_write(change, root=tmp_path)
    ledger_after = read_ledger(change, root=tmp_path)
    row = ledger_after["entries"][0]
    assert row["check_id"] == "opsx-pg-provider-receipt"
    assert row["state"] == "FAIL"
    assert row.get("last_result") == "BLOCKED"

    terminal = classify_next_check(
        repo=tmp_path, change=change, raw_output="", completed=True
    )
    assert terminal.status == TERMINAL_BLOCKED_CHECK


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        test_pg_provider_blocked_drains_with_last_result(Path(tmp))
    print("pg provider convergence guard OK")
