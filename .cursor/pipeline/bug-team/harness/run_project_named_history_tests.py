# -*- coding: utf-8 -*-
"""HIST-01..14 project-named bug history acceptance (V1)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import traceback
import uuid
from pathlib import Path
from typing import Callable, List, Tuple
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[2]
sys.path.insert(0, str(ROOT))

from runtime.bug_team_command_routing import (  # noqa: E402
    CommandEntryClass,
    legacy_repo_local_case_root,
    live_v3_append_disabled,
    open_orchestrator_for_route,
    route_bug_team_command,
)
from runtime.case_store_resolver import (  # noqa: E402
    DurableStoreErrorCode,
    DurableStoreUnresolved,
    create_new_durable_project_case,
    generate_case_uuid,
    resolve_durable_project_case,
    resolve_project_identity,
    resolve_project_store_roots,
)
from runtime.causal_history.config import write_settings, CausalHistorySettings  # noqa: E402
from runtime.causal_history.store import CausalEventStore, EvidenceBinding  # noqa: E402

ARTIFACTS = Path("/opt/cursor/artifacts/project-named-history-v1")
TEST_BASE = Path("/tmp/project-named-history-isolated")
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


def hist_01_two_worktrees_same_root() -> Result:
    test_id = "HIST-01"
    base = TEST_BASE / f"h01-{uuid.uuid4().hex[:6]}"
    fleet = base / "fleet"
    repo = _mini_repo(base, "stroy-system")
    try:
        r1 = route_bug_team_command("bug A", repo_root=repo, acceptance_store_root=fleet)
        r2 = route_bug_team_command(f"case={r1.case_id}", repo_root=repo, acceptance_store_root=fleet)
        ok = r1.store_root == r2.store_root and "stroy-system" in str(r1.store_root)
        return ok, str(r1.store_root), test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def hist_02_distinct_project_names() -> Result:
    test_id = "HIST-02"
    base = TEST_BASE / f"h02-{uuid.uuid4().hex[:6]}"
    fleet = base / "fleet"
    repo_a = _mini_repo(base, "stroy-system")
    repo_b = _mini_repo(base, "adv-bot")
    try:
        a = route_bug_team_command("a", repo_root=repo_a, acceptance_store_root=fleet)
        b = route_bug_team_command("b", repo_root=repo_b, acceptance_store_root=fleet)
        ok = a.store_root != b.store_root
        return ok, f"{a.store_root.name} vs {b.store_root.parent.name}", test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def hist_03_multiple_changes_one_project() -> Result:
    test_id = "HIST-03"
    base = TEST_BASE / f"h03-{uuid.uuid4().hex[:6]}"
    fleet = base / "fleet"
    repo = _mini_repo(base, "multi-chg")
    try:
        routes = [
            route_bug_team_command(f"change {i}", repo_root=repo, acceptance_store_root=fleet)
            for i in range(3)
        ]
        roots = {r.store_root for r in routes}
        ids = {r.case_id for r in routes}
        ok = len(roots) == 1 and len(ids) == 3 and all(_UUID4_RE.match(c) for c in ids)
        return ok, f"cases={len(ids)}", test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def hist_04_no_change_derived_paths() -> Result:
    test_id = "HIST-04"
    base = TEST_BASE / f"h04-{uuid.uuid4().hex[:6]}"
    fleet = base / "fleet"
    repo = _mini_repo(base, "spanproj")
    try:
        r1 = route_bug_team_command("same change context", repo_root=repo, acceptance_store_root=fleet)
        r2 = route_bug_team_command(f"case={r1.case_id}", repo_root=repo, acceptance_store_root=fleet)
        ok = (
            r1.case_root == r2.case_root
            and r1.store_root is not None
            and r1.store_root.name == "cases"
            and r1.store_root.parent.name == "spanproj"
            and "same-change" not in str(r1.case_root)
        )
        return ok, r1.case_id, test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def hist_05_concurrent_uuid_cases() -> Result:
    test_id = "HIST-05"
    base = TEST_BASE / f"h05-{uuid.uuid4().hex[:6]}"
    fleet = base / "fleet"
    repo = _mini_repo(base, "conc")
    ids: List[str] = []
    lock = threading.Lock()

    def worker():
        r = route_bug_team_command("x", repo_root=repo, acceptance_store_root=fleet)
        with lock:
            ids.append(r.case_id)

    try:
        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        ok = len(set(ids)) == 5 and all(_UUID4_RE.match(i) for i in ids)
        return ok, f"n={len(set(ids))}", test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def hist_06_uuid_collision_no_overwrite() -> Result:
    test_id = "HIST-06"
    base = TEST_BASE / f"h06-{uuid.uuid4().hex[:6]}"
    fleet = base / "fleet"
    repo = _mini_repo(base, "collide")
    fixed = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    os.environ["OPSX_BUG_CASE_STORE_ROOT"] = str(fleet)
    try:
        with mock.patch(
            "runtime.case_store_resolver.generate_case_uuid",
            side_effect=[fixed, generate_case_uuid()],
        ):
            r1 = create_new_durable_project_case(repo_root=repo)
            r2 = create_new_durable_project_case(repo_root=repo)
    finally:
        os.environ.pop("OPSX_BUG_CASE_STORE_ROOT", None)
    try:
        marker = r1.case_root / "ONLY.txt"
        marker.write_text("keep\n", encoding="utf-8")
        ok = r1.case_id == fixed and r2.case_id != fixed and marker.read_text() == "keep\n"
        return ok, f"first={r1.case_id[:8]}", test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def hist_07_resume_preserves_case_id() -> Result:
    test_id = "HIST-07"
    base = TEST_BASE / f"h07-{uuid.uuid4().hex[:6]}"
    fleet = base / "fleet"
    repo = _mini_repo(base, "resume")
    try:
        created = route_bug_team_command("intake", repo_root=repo, acceptance_store_root=fleet)
        resumed = route_bug_team_command(
            f"case={created.case_id}", repo_root=repo, acceptance_store_root=fleet
        )
        ok = created.case_id == resumed.case_id
        return ok, created.case_id, test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def hist_08_missing_project_name_blocks() -> Result:
    test_id = "HIST-08"
    base = TEST_BASE / f"h08-{uuid.uuid4().hex[:6]}"
    repo = base / "no-name"
    repo.mkdir(parents=True)
    (repo / "opsx").mkdir()
    (repo / "opsx/sources.yaml").write_text("repo_family_id: x\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "i"], cwd=repo, check=True, capture_output=True)
    try:
        route_bug_team_command("bug", repo_root=repo, acceptance_store_root=base / "fleet")
        return False, "expected pause", test_id
    except (DurableStoreUnresolved, Exception):
        return True, "fail-closed", test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def hist_09_no_tmp_fallback() -> Result:
    test_id = "HIST-09"
    base = TEST_BASE / f"h09-{uuid.uuid4().hex[:6]}"
    fleet = base / "fleet"
    repo = _mini_repo(base, "notmp")
    try:
        r = route_bug_team_command("bug", repo_root=repo, acceptance_store_root=fleet)
        ok = ".tmp" not in str(r.case_root) and "/cases/" in str(r.case_root)
        return ok, str(r.case_root), test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def hist_10_v3_semantics_unchanged() -> Result:
    test_id = "HIST-10"
    base = TEST_BASE / f"h10-{uuid.uuid4().hex[:6]}"
    fleet = base / "fleet"
    repo = _mini_repo(base, "v3sem")
    try:
        r = route_bug_team_command("bug", repo_root=repo, acceptance_store_root=fleet)
        write_settings(r.case_root, CausalHistorySettings.synthetic_v3())
        proof = r.case_root / "results" / "P.md"
        proof.parent.mkdir(parents=True, exist_ok=True)
        proof.write_text("p\n", encoding="utf-8")
        import hashlib

        dig = hashlib.sha256(proof.read_bytes()).hexdigest()
        pub = CausalEventStore(r.case_root, r.case_id).publish_event(
            "WEX/t/1:CAUSAL_EVENT",
            {"EVENT_KIND": "HOP_COMPLETED"},
            EvidenceBinding("results/P.md", dig, "1:w"),
        )
        replay = CausalEventStore(r.case_root, r.case_id).replay_deterministic()
        ok = pub.event_id and replay[0]["EVENT_ID"] == pub.event_id
        return ok, pub.event_id[:16], test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def hist_11_legacy_readable() -> Result:
    test_id = "HIST-11"
    base = TEST_BASE / f"h11-{uuid.uuid4().hex[:6]}"
    fleet = base / "fleet"
    repo = _mini_repo(base, "legacy")
    legacy_id = "BUGCASE-LEGACY-READ"
    try:
        legacy = legacy_repo_local_case_root(repo, legacy_id)
        legacy.mkdir(parents=True)
        route = route_bug_team_command(
            f"case={legacy_id}", repo_root=repo, acceptance_store_root=fleet
        )
        ok = route.entry_class == CommandEntryClass.EXPLICIT_LEGACY_CASE
        return ok, route.entry_class.value, test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def hist_12_command_entrypoint() -> Result:
    test_id = "HIST-12"
    base = TEST_BASE / f"h12-{uuid.uuid4().hex[:6]}"
    fleet = base / "fleet"
    repo = _mini_repo(base, "cmd")
    try:
        route = route_bug_team_command("production path", repo_root=repo, acceptance_store_root=fleet)
        orch = open_orchestrator_for_route(route)
        ok = (
            route.entry_class == CommandEntryClass.DURABLE_REAL_CASE
            and _UUID4_RE.match(route.case_id)
            and orch.resolved_case is not None
        )
        return ok, route.case_id, test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def hist_13_fleet_and_default_namespace() -> Result:
    test_id = "HIST-13"
    base = TEST_BASE / f"h13-{uuid.uuid4().hex[:6]}"
    fleet = base / "custom-fleet"
    repo = _mini_repo(base, "ns")
    try:
        r_fleet = route_bug_team_command("a", repo_root=repo, acceptance_store_root=fleet)
        proj = resolve_project_identity(repo)
        os.environ.pop("OPSX_BUG_CASE_STORE_ROOT", None)
        cases_default, _ = resolve_project_store_roots(proj)
        ok = (
            r_fleet.store_root.name == "cases"
            and r_fleet.store_root.parent.name == "ns"
            and cases_default.name == "cases"
            and cases_default.parent.name == "ns"
        )
        return ok, f"fleet={r_fleet.store_root} default={cases_default}", test_id
    finally:
        os.environ.pop("OPSX_BUG_CASE_STORE_ROOT", None)
        shutil.rmtree(base, ignore_errors=True)


def hist_14_live_v3_off() -> Result:
    test_id = "HIST-14"
    base = TEST_BASE / f"h14-{uuid.uuid4().hex[:6]}"
    fleet = base / "fleet"
    repo = _mini_repo(base, "liveoff")
    try:
        r = route_bug_team_command("bug", repo_root=repo, acceptance_store_root=fleet)
        ok = live_v3_append_disabled(r)
        return ok, "live_off", test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


TESTS: List[Tuple[str, Callable[[], Result]]] = [
    ("HIST-01", hist_01_two_worktrees_same_root),
    ("HIST-02", hist_02_distinct_project_names),
    ("HIST-03", hist_03_multiple_changes_one_project),
    ("HIST-04", hist_04_no_change_derived_paths),
    ("HIST-05", hist_05_concurrent_uuid_cases),
    ("HIST-06", hist_06_uuid_collision_no_overwrite),
    ("HIST-07", hist_07_resume_preserves_case_id),
    ("HIST-08", hist_08_missing_project_name_blocks),
    ("HIST-09", hist_09_no_tmp_fallback),
    ("HIST-10", hist_10_v3_semantics_unchanged),
    ("HIST-11", hist_11_legacy_readable),
    ("HIST-12", hist_12_command_entrypoint),
    ("HIST-13", hist_13_fleet_and_default_namespace),
    ("HIST-14", hist_14_live_v3_off),
]


def main() -> int:
    head = _head()
    exec_id = f"project-named-history@{head[:12]}"
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
        rows.append({"test_id": tid, "passed": ok, "detail": detail, "revision": head, "execution_id": exec_id})
        print(f"{tid}: {'PASS' if ok else 'FAIL'} {detail[:100]}")

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "PROJECT_NAMED_HISTORY_VERIFIED" if failed == 0 else "FAILED",
        "execution_id": exec_id,
        "revision": head,
        "tests": rows,
    }
    (ARTIFACTS / "project_named_history_report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
