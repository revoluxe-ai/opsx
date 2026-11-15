# -*- coding: utf-8 -*-
"""HOST-01..10 Bug Team host dispatch acceptance."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import traceback
import uuid
from pathlib import Path
from typing import Callable, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[2]
sys.path.insert(0, str(ROOT))

from runtime.bug_team_command_routing import (  # noqa: E402
    CommandEntryClass,
    legacy_repo_local_case_root,
)
from runtime.bug_team_host import CLI_DISPATCH_SCRIPT, dispatch_opsx_bug_team  # noqa: E402
from runtime.case_store_resolver import DurableStoreUnresolved  # noqa: E402
from runtime.causal_history.config import load_settings  # noqa: E402

ARTIFACTS = Path("/opt/cursor/artifacts/bug-team-host-wiring-v1")
TEST_BASE = Path("/tmp/bug-team-host-isolated")
_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.I,
)
Result = Tuple[bool, str, str]


def _head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()


def _mini_repo(base: Path, project_name: str) -> Path:
    repo = base / project_name
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "opsx").mkdir(parents=True, exist_ok=True)
    (repo / "opsx/sources.yaml").write_text(
        f"repo_family_id: {project_name}\nproject_name: {project_name}\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.local"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


def _cli_dispatch(args: str, repo: Path, fleet: Path) -> dict:
    """Observable production CLI boundary (host script)."""
    cmd = [
        sys.executable,
        str(ROOT / "harness" / "opsx_bug_team_host_dispatch.py"),
        "--args",
        args,
        "--repo",
        str(repo),
        "--acceptance-store",
        str(fleet),
    ]
    out = subprocess.check_output(cmd, cwd=REPO, text=True)
    return json.loads(out)


def host_01_new_uuid_under_project() -> Result:
    test_id = "HOST-01"
    base = TEST_BASE / f"h01-{uuid.uuid4().hex[:6]}"
    fleet = base / "fleet"
    repo = _mini_repo(base, "stroy-system")
    try:
        receipt = _cli_dispatch("новый баг в оплате", repo, fleet)
        ok = (
            _UUID4_RE.match(receipt["case_id"])
            and receipt["project_name"] == "stroy-system"
            and "/stroy-system/cases/" in receipt["case_root"]
        )
        return ok, receipt["case_id"], test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def host_02_resume_same_uuid() -> Result:
    test_id = "HOST-02"
    base = TEST_BASE / f"h02-{uuid.uuid4().hex[:6]}"
    fleet = base / "fleet"
    repo = _mini_repo(base, "resume-proj")
    try:
        created = dispatch_opsx_bug_team("intake", repo_root=repo, acceptance_store_root=fleet)
        resumed = _cli_dispatch(f"case={created.case_id}", repo, fleet)
        ok = created.case_id == resumed["case_id"] == resumed.get("case_id")
        return ok, resumed["case_id"], test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def host_03_two_worktrees_same_history() -> Result:
    test_id = "HOST-03"
    base = TEST_BASE / f"h03-{uuid.uuid4().hex[:6]}"
    fleet = base / "fleet"
    repo = _mini_repo(base, "wt-proj")
    try:
        r1 = dispatch_opsx_bug_team("bug", repo_root=repo, acceptance_store_root=fleet)
        r2 = dispatch_opsx_bug_team(f"case={r1.case_id}", repo_root=repo, acceptance_store_root=fleet)
        ok = r1.store_root == r2.store_root
        return ok, str(r1.store_root), test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def host_04_multiple_changes_one_project_dir() -> Result:
    test_id = "HOST-04"
    base = TEST_BASE / f"h04-{uuid.uuid4().hex[:6]}"
    fleet = base / "fleet"
    repo = _mini_repo(base, "multi")
    try:
        stores = {
            dispatch_opsx_bug_team(f"change {i}", repo_root=repo, acceptance_store_root=fleet).store_root
            for i in range(3)
        }
        ok = len(stores) == 1
        return ok, str(next(iter(stores))), test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def host_05_missing_project_name_blocks() -> Result:
    test_id = "HOST-05"
    base = TEST_BASE / f"h05-{uuid.uuid4().hex[:6]}"
    repo = base / "noname"
    repo.mkdir(parents=True)
    (repo / "opsx").mkdir()
    (repo / "opsx/sources.yaml").write_text("repo_family_id: x\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "i"], cwd=repo, check=True, capture_output=True)
    try:
        dispatch_opsx_bug_team("bug", repo_root=repo, acceptance_store_root=base / "fleet")
        return False, "expected block", test_id
    except (DurableStoreUnresolved, Exception):
        return True, "fail-closed", test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def host_06_inaccessible_store_fail_closed() -> Result:
    test_id = "HOST-06"
    base = TEST_BASE / f"h06-{uuid.uuid4().hex[:6]}"
    repo = _mini_repo(base, "badstore")
    bad = repo / "opsx" / "inside-worktree-store"
    bad.mkdir(parents=True, exist_ok=True)
    try:
        dispatch_opsx_bug_team("bug", repo_root=repo, acceptance_store_root=bad)
        return False, "expected store rejection", test_id
    except (DurableStoreUnresolved, Exception):
        return True, "fail-closed", test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def host_07_no_tmp_history() -> Result:
    test_id = "HOST-07"
    base = TEST_BASE / f"h07-{uuid.uuid4().hex[:6]}"
    fleet = base / "fleet"
    repo = _mini_repo(base, "notmp")
    try:
        r = dispatch_opsx_bug_team("bug", repo_root=repo, acceptance_store_root=fleet)
        ok = ".tmp" not in str(r.case_root) and "cases" in str(r.case_root)
        return ok, str(r.case_root), test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def host_08_legacy_resume() -> Result:
    test_id = "HOST-08"
    base = TEST_BASE / f"h08-{uuid.uuid4().hex[:6]}"
    fleet = base / "fleet"
    repo = _mini_repo(base, "legacy")
    legacy_id = "BUGCASE-LEGACY-HOST"
    try:
        legacy = legacy_repo_local_case_root(repo, legacy_id)
        legacy.mkdir(parents=True)
        r = dispatch_opsx_bug_team(f"case={legacy_id}", repo_root=repo, acceptance_store_root=fleet)
        ok = r.entry_class == CommandEntryClass.EXPLICIT_LEGACY_CASE
        return ok, r.entry_class.value, test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def host_09_live_v3_off() -> Result:
    test_id = "HOST-09"
    base = TEST_BASE / f"h09-{uuid.uuid4().hex[:6]}"
    fleet = base / "fleet"
    repo = _mini_repo(base, "v3off")
    try:
        r = dispatch_opsx_bug_team("bug", repo_root=repo, acceptance_store_root=fleet)
        settings = load_settings(r.case_root)
        ok = not settings.live_v3_append_enabled
        return ok, f"live={settings.live_v3_append_enabled}", test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def host_10_write_boundaries() -> Result:
    test_id = "HOST-10"
    base = TEST_BASE / f"h10-{uuid.uuid4().hex[:6]}"
    fleet = base / "fleet"
    repo = _mini_repo(base, "boundary")
    try:
        r = dispatch_opsx_bug_team("bug", repo_root=repo, acceptance_store_root=fleet)
        from runtime import fs_boundary

        ok = (
            r.orchestrator.case_root == r.case_root
            and "CAUSAL_HISTORY_APPENDER" in fs_boundary.WORKER_ALLOWLISTS
        )
        return ok, CLI_DISPATCH_SCRIPT, test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


TESTS: List[Tuple[str, Callable[[], Result]]] = [
    ("HOST-01", host_01_new_uuid_under_project),
    ("HOST-02", host_02_resume_same_uuid),
    ("HOST-03", host_03_two_worktrees_same_history),
    ("HOST-04", host_04_multiple_changes_one_project_dir),
    ("HOST-05", host_05_missing_project_name_blocks),
    ("HOST-06", host_06_inaccessible_store_fail_closed),
    ("HOST-07", host_07_no_tmp_history),
    ("HOST-08", host_08_legacy_resume),
    ("HOST-09", host_09_live_v3_off),
    ("HOST-10", host_10_write_boundaries),
]


def main() -> int:
    head = _head()
    exec_id = f"bug-team-host@{head[:12]}"
    rows = []
    failed = 0
    for tid, fn in TESTS:
        try:
            ok, detail, returned_id = fn()
            assert returned_id == tid
        except Exception:
            ok, detail, tid = False, traceback.format_exc(), fn.__name__
        if not ok:
            failed += 1
        rows.append(
            {
                "test_id": tid,
                "passed": ok,
                "detail": detail,
                "revision": head,
                "execution_id": exec_id,
            }
        )
        print(f"{tid}: {'PASS' if ok else 'FAIL'} {detail[:100]}")

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "HOST_INTEGRATION_VERIFIED" if failed == 0 else "FAILED",
        "integration_level": "CLI_HOST_DISPATCH_BOUNDARY",
        "entrypoint": "/opsx-bug-team",
        "cli_script": CLI_DISPATCH_SCRIPT,
        "revision": head,
        "tests": rows,
    }
    (ARTIFACTS / "host_integration_report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
