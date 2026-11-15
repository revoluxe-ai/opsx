# -*- coding: utf-8
"""Recovery-log crash hook must leave prior log intact."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BV_ROOT = REPO_ROOT / ".cursor" / "pipeline" / "bounded-verify"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(BV_ROOT))

from bounded_verify_executor import run_current_check  # noqa: E402
from bounded_verify_ledger import BoundedVerifyLedgerError, schedule_write  # noqa: E402
from bounded_verify_plan import draft_plan, freeze_plan  # noqa: E402
from bounded_verify_recovery import authorize_check_recovery  # noqa: E402
from test_impl_seed import seed_hermetic_impl_ok  # noqa: E402


def test_recovery_crash_before_replace_preserves_log() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        shutil.copytree(REPO_ROOT / "opsx/project", root / "opsx/project", dirs_exist_ok=True)
        fixture = json.loads((FIXTURES / "minimal_change_tree.json").read_text(encoding="utf-8"))
        change_id = str(fixture["change_id"])
        cdir = root / "openspec/changes" / change_id
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "tasks.md").write_text(str(fixture["tasks_md"]), encoding="utf-8")
        seed_hermetic_impl_ok(change_id, root=root)
        registry = FIXTURES / "registry_snapshot.yaml"
        draft_plan(change_id, [{"check_id": "pyright-strict", "order": 1}], root=root, registry_path=registry)
        freeze_plan(change_id, root=root, registry_path=registry)
        hop = schedule_write(change_id, expected_revision=1, root=root)
        run_current_check(
            change_id,
            check_id=hop.current_check_id,
            assignment_epoch=hop.assignment_epoch,
            ledger_revision=hop.ledger_revision,
            execution_id=str(uuid.uuid4()),
            root=root,
            registry_path=registry,
            check_runner=lambda *_a: ("FAIL", {"STATUS": "FAIL"}),
        )
        schedule_write(change_id, expected_revision=hop.ledger_revision, root=root)
        os.environ["BOUNDED_VERIFY_RECOVERY_CRASH"] = "before_replace"
        try:
            authorize_check_recovery(
                change_id,
                "pyright-strict",
                causal_finding="TECHNICAL_RETRY: crash test",
                technical_retry=True,
                root=root,
            )
        except BoundedVerifyLedgerError:
            pass
        finally:
            os.environ.pop("BOUNDED_VERIFY_RECOVERY_CRASH", None)
        log_path = cdir / "bounded-verify/recovery-log.json"
        assert not log_path.is_file() or json.loads(log_path.read_text())


def main() -> int:
    test_recovery_crash_before_replace_preserves_log()
    print("recovery crash hook tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
