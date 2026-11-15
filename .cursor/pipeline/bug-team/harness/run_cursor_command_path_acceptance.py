# -*- coding: utf-8 -*-
"""Cursor command-path acceptance (CLI dispatch boundary only).

Simulates the documented /opsx-bug-team host step:

  python .cursor/pipeline/bug-team/harness/opsx_bug_team_host_dispatch.py ...

Does NOT invoke Cursor Task workers or direct ``dispatch_opsx_bug_team`` calls.
"""

from __future__ import annotations

import json
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
CLI = ROOT / "harness" / "opsx_bug_team_host_dispatch.py"
CMD_MD = REPO / ".cursor/commands/opsx-bug-team.md"
TEST_BASE = Path("/tmp/cursor-command-path-acceptance")
ARTIFACTS = Path("/opt/cursor/artifacts/cursor-command-path-v1")
_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.I,
)
Result = Tuple[bool, str, str]


def _head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()


def _mini_repo(base: Path, name: str) -> Path:
    repo = base / name
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "opsx").mkdir(parents=True, exist_ok=True)
    (repo / "opsx/sources.yaml").write_text(
        f"repo_family_id: {name}\nproject_name: {name}\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.local"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


def _cli(args: str, repo: Path, fleet: Path, *, expect_fail: bool = False) -> dict:
    cmd = [
        sys.executable,
        str(CLI),
        "--args",
        args,
        "--repo",
        str(repo),
        "--acceptance-store",
        str(fleet),
    ]
    proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    if expect_fail:
        if proc.returncode == 0:
            raise AssertionError(f"expected CLI failure, got stdout={proc.stdout}")
        return {"stderr": proc.stderr, "returncode": proc.returncode}
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    return json.loads(proc.stdout)


def ccp_01_checkout_has_host_commit() -> Result:
    test_id = "CCP-01"
    host = "35aa14a86711a97d3bbb592879400ef1eda5c391"
    ok = subprocess.call(["git", "merge-base", "--is-ancestor", host, "HEAD"], cwd=REPO) == 0
    return ok, _head(), test_id


def ccp_02_command_contract_documents_cli() -> Result:
    test_id = "CCP-02"
    text = CMD_MD.read_text(encoding="utf-8")
    ok = (
        "opsx_bug_team_host_dispatch.py" in text
        and "HOST_DISPATCH_MODULE" in text
        and "mandatory before worker dispatch" in text.lower()
    )
    return ok, str(CMD_MD), test_id


def ccp_03_cli_new_case_uuid_path() -> Result:
    test_id = "CCP-03"
    base = TEST_BASE / f"ccp03-{uuid.uuid4().hex[:6]}"
    fleet = base / "fleet"
    repo = _mini_repo(base, "stroy-system")
    try:
        receipt = _cli("тестовый баг для acceptance", repo, fleet)
        ok = (
            receipt.get("host_dispatch_module") == "runtime.bug_team_host.dispatch_opsx_bug_team"
            and _UUID4_RE.match(receipt["case_id"])
            and "/stroy-system/cases/" in receipt["case_root"]
        )
        return ok, receipt["case_id"], test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def ccp_04_resume_same_case_root() -> Result:
    test_id = "CCP-04"
    base = TEST_BASE / f"ccp04-{uuid.uuid4().hex[:6]}"
    fleet = base / "fleet"
    repo = _mini_repo(base, "resume-cli")
    try:
        created = _cli("новый кейс", repo, fleet)
        resumed = _cli(f"case={created['case_id']}", repo, fleet)
        ok = (
            created["case_id"] == resumed["case_id"]
            and created["case_root"] == resumed["case_root"]
        )
        return ok, resumed["case_id"], test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def ccp_05_no_silent_case_root_drift() -> Result:
    test_id = "CCP-05"
    base = TEST_BASE / f"ccp05-{uuid.uuid4().hex[:6]}"
    fleet = base / "fleet"
    repo = _mini_repo(base, "drift")
    try:
        r1 = _cli("one", repo, fleet)
        r2 = _cli(f"case={r1['case_id']}", repo, fleet)
        ok = r1["case_root"] == r2["case_root"] and r1["case_id"] == r2["case_id"]
        return ok, r1["case_root"], test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def ccp_06_dispatch_failure_blocks_cli() -> Result:
    test_id = "CCP-06"
    base = TEST_BASE / f"ccp06-{uuid.uuid4().hex[:6]}"
    repo = base / "noname"
    repo.mkdir(parents=True)
    (repo / "opsx").mkdir()
    (repo / "opsx/sources.yaml").write_text("repo_family_id: x\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "i"], cwd=repo, check=True, capture_output=True)
    try:
        _cli("bug", repo, base / "fleet", expect_fail=True)
        return True, "non-zero exit", test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def ccp_07_cli_entrypoint_from_repo_root() -> Result:
    test_id = "CCP-07"
    rel = ".cursor/pipeline/bug-team/harness/opsx_bug_team_host_dispatch.py"
    ok = (REPO / rel).is_file()
    return ok, rel, test_id


def ccp_08_receipt_revision_bound() -> Result:
    test_id = "CCP-08"
    base = TEST_BASE / f"ccp08-{uuid.uuid4().hex[:6]}"
    fleet = base / "fleet"
    try:
        receipt = _cli("rev bind", REPO, fleet)
        ok = receipt.get("revision") == _head() and receipt.get("entrypoint") == "/opsx-bug-team"
        return ok, receipt.get("revision", "")[:12], test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


TESTS: List[Tuple[str, Callable[[], Result]]] = [
    ("CCP-01", ccp_01_checkout_has_host_commit),
    ("CCP-02", ccp_02_command_contract_documents_cli),
    ("CCP-03", ccp_03_cli_new_case_uuid_path),
    ("CCP-04", ccp_04_resume_same_case_root),
    ("CCP-05", ccp_05_no_silent_case_root_drift),
    ("CCP-06", ccp_06_dispatch_failure_blocks_cli),
    ("CCP-07", ccp_07_cli_entrypoint_from_repo_root),
    ("CCP-08", ccp_08_receipt_revision_bound),
]


def main() -> int:
    head = _head()
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
        rows.append({"test_id": tid, "passed": ok, "detail": detail, "revision": head})
        print(f"{tid}: {'PASS' if ok else 'FAIL'} {detail[:120]}")

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "CURSOR_COMMAND_PATH_VERIFIED" if failed == 0 else "FAILED",
        "cli_dispatch_verified": failed == 0,
        "command_contract_verified": rows[1]["passed"] if len(rows) > 1 else False,
        "cursor_chat_runtime_observed": False,
        "cursor_chat_runtime_note": "CURSOR_CHAT_RUNTIME_UNVERIFIED",
        "revision": head,
        "cli_script": str(CLI.relative_to(REPO)),
        "tests": rows,
    }
    (ARTIFACTS / "cursor_command_path_report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
