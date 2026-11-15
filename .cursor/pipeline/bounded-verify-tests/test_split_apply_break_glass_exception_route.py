# -*- coding: utf-8 -*-
"""Break-glass exception route allows compose DRAFT with stale GREEN recorded."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BV_ROOT = REPO_ROOT / ".cursor" / "pipeline" / "bounded-verify"
FIXTURE = REPO_ROOT / "tests/unit/fixtures/redImplementGreenLifecycle"
sys.path.insert(0, str(BV_ROOT))

from bounded_verify_plan import BoundedVerifyPlanError  # noqa: E402
from bounded_verify_split_apply import split_apply_green_identity  # noqa: E402
from verification_plan_compose import compose_verification_plan  # noqa: E402


def _activate_break_glass(root: Path, change_id: str, revision: str) -> None:
    env = {
        **dict(**__import__("os").environ),
        "BG_CHANGE_ID": change_id,
        "BG_CHANGE_ROOT": f"openspec/changes/{change_id}",
        "BG_BLOCKED_GATE": "ANY",
        "BG_FAILURE_CODE": "STALE_GREEN",
        "BG_REVISION": revision,
        "BG_BRANCH": "master",
    }
    proc = subprocess.run(
        ["node", "opsx_tools/opsx-break-glass-cli.mjs"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    payload = json.loads(proc.stdout)
    assert payload.get("ok") is True


def test_stale_green_compose_draft_under_break_glass() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        change = "ecnp-like-stale"
        cdir = root / "openspec/changes" / change
        design_dst = cdir / "apply-design"
        design_dst.mkdir(parents=True)
        design = json.loads((FIXTURE / "APPLY-DESIGN.json").read_text(encoding="utf-8"))
        design["change_id"] = change
        (design_dst / "APPLY-DESIGN.json").write_text(
            json.dumps(design) + "\n",
            encoding="utf-8",
        )
        (cdir / "tasks.md").write_text("- [x] impl done\n", encoding="utf-8")
        green_dir = cdir / "apply-green/SLICE-001"
        green_dir.mkdir(parents=True)
        green = {
            "status": "GREEN_PROVEN",
            "slice_id": "SLICE-001",
            "apply_design_identity": {"planning_digest": "deadbeef"},
            "frozen_test_content_identity": "mismatch",
            "red_final_status": "PASS",
        }
        (green_dir / "GREEN.json").write_text(json.dumps(green) + "\n", encoding="utf-8")
        shutil.copytree(REPO_ROOT / "opsx_tools", root / "opsx_tools", dirs_exist_ok=True)
        bg_dir = root / "opsx/break-glass"
        bg_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(
            REPO_ROOT / "opsx/break-glass/capability-backlog.ndjson",
            bg_dir / "capability-backlog.ndjson",
        )
        shutil.copytree(
            REPO_ROOT / ".cursor/pipeline/bounded-verify",
            root / ".cursor/pipeline/bounded-verify",
            dirs_exist_ok=True,
        )
        shutil.copytree(
            REPO_ROOT / ".cursor/static-check",
            root / ".cursor/static-check",
            dirs_exist_ok=True,
        )
        for rel in (
            "openspec/policies/VERIFY_POLICY.md",
            "opsx/project/capabilities.yaml",
        ):
            src = REPO_ROOT / rel
            if src.is_file():
                dst = root / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

        subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "bg@test"],
            cwd=root,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "bg"],
            cwd=root,
            check=True,
            capture_output=True,
        )
        subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=root,
            check=True,
            capture_output=True,
        )
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        try:
            split_apply_green_identity(change, root=root)
        except BoundedVerifyPlanError as exc:
            assert exc.code == "SPLIT_APPLY_STALE"
        else:
            raise AssertionError("expected STALE without break glass")

        _activate_break_glass(root, change, revision)
        deferral = cdir / "apply-process-exception/PROCESS-GATE-DEFERRAL.json"
        assert deferral.is_file()

        identity = split_apply_green_identity(change, root=root)
        assert identity["policy"] == "SPLIT_APPLY_CURRENT_GREEN"

        composed = compose_verification_plan(change, "static_fast", root=root)
        assert composed.draft.plan_status == "DRAFT"
        deferral_doc = json.loads(deferral.read_text(encoding="utf-8"))
        assert deferral_doc.get("provenance") == "OWNER_OVERRIDE"
        stale = deferral_doc.get("actual_gate_results", {}).get(
            "split_apply_green_freshness", {}
        )
        assert stale.get("status") == "FAILED"
        assert stale.get("minted_fake_green") is False


def main() -> int:
    test_stale_green_compose_draft_under_break_glass()
    print("break glass split-apply exception route tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
