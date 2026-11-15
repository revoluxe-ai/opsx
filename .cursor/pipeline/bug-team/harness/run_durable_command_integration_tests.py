# -*- coding: utf-8 -*-
"""CMD-01..12 command-path acceptance for durable bug case routing V0."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
import uuid
from pathlib import Path
from typing import Callable, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[2]
sys.path.insert(0, str(ROOT))

from runtime.bug_team_command_routing import (  # noqa: E402
    CommandEntryClass,
    CommandRoutingBlocked,
    allocate_bug_case_id,
    legacy_repo_local_case_root,
    live_v3_append_disabled,
    open_orchestrator_for_route,
    read_durable_case_history,
    route_bug_team_command,
    route_evidence_digest,
    assert_no_repo_local_fallback_for_real,
)
from runtime.case_store_resolver import (  # noqa: E402
    CaseRootMode,
    DurableStoreUnresolved,
    resolve_project_identity,
)
from runtime.causal_history.config import write_settings, CausalHistorySettings  # noqa: E402
from runtime.causal_history.store import CausalEventStore, EvidenceBinding  # noqa: E402
from runtime import fs_boundary  # noqa: E402

ARTIFACTS = Path("/opt/cursor/artifacts/durable-command-integration-v0")
TEST_BASE = Path("/tmp/durable-command-integration-isolated")
Result = Tuple[bool, str, str]


def _head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()


def _mini_repo(base: Path, family_id: str, remote_url: str) -> Path:
    repo = base / family_id
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "opsx").mkdir(parents=True, exist_ok=True)
    (repo / "opsx/sources.yaml").write_text(
        f"repo_family_id: {family_id}\nproject_name: {family_id}\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.local"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "remote.origin.url", remote_url], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


def cmd_01_new_real_case_durable() -> Result:
    test_id = "CMD-01"
    base = TEST_BASE / f"cmd01-{uuid.uuid4().hex[:8]}"
    fleet = base / "fleet"
    repo = _mini_repo(base, "cmd01-proj", "https://github.com/org/cmd01.git")
    try:
        route = route_bug_team_command(
            "баг в оплате",
            repo_root=repo,
            acceptance_store_root=fleet,
        )
        assert_no_repo_local_fallback_for_real(route)
        ok = (
            route.entry_class == CommandEntryClass.DURABLE_REAL_CASE
            and route.case_root.is_dir()
            and route.project_id is not None
            and route.store_root is not None
        )
        return ok, route_evidence_digest(route), test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def cmd_02_second_worktree_same_history() -> Result:
    test_id = "CMD-02"
    base = TEST_BASE / f"cmd02-{uuid.uuid4().hex[:8]}"
    fleet = base / "fleet"
    repo = _mini_repo(base, "cmd02-proj", "https://github.com/org/cmd02.git")
    try:
        r1 = route_bug_team_command("intake", repo_root=repo, acceptance_store_root=fleet)
        (r1.case_root / "team" / "MARKER.txt").parent.mkdir(parents=True, exist_ok=True)
        (r1.case_root / "team" / "MARKER.txt").write_text("hop1\n", encoding="utf-8")
        r2 = route_bug_team_command(
            f"case={r1.case_id}",
            repo_root=repo,
            acceptance_store_root=fleet,
        )
        ok = r2.entry_class == CommandEntryClass.DURABLE_REAL_CASE and r2.case_root == r1.case_root
        return ok, str(r2.case_root), test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def cmd_03_distinct_project_same_case_id() -> Result:
    test_id = "CMD-03"
    base = TEST_BASE / f"cmd03-{uuid.uuid4().hex[:8]}"
    fleet = base / "fleet"
    case_id = "BUGCASE-SHARED-ID"
    repo_a = _mini_repo(base, "proj-a", "https://github.com/org/a.git")
    repo_b = _mini_repo(base, "proj-b", "https://github.com/org/b.git")
    try:
        from runtime.case_store_resolver import resolve_durable_project_case

        with _env_store(fleet):
            a = resolve_durable_project_case(case_id, repo_root=repo_a)
            (a.case_root / "SECRET.txt").write_text("a\n", encoding="utf-8")
            b = resolve_durable_project_case(case_id, repo_root=repo_b)
            (b.case_root / "SECRET.txt").write_text("b\n", encoding="utf-8")
        ok = a.case_root != b.case_root and (a.case_root / "SECRET.txt").read_text() == "a\n"
        return ok, f"a={a.project.project_id} b={b.project.project_id}", test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _env_store(store: Path):
    class _Ctx:
        def __enter__(self):
            self.prev = os.environ.get("OPSX_BUG_CASE_STORE_ROOT")
            os.environ["OPSX_BUG_CASE_STORE_ROOT"] = str(store)
            return store

        def __exit__(self, *args):
            if self.prev is None:
                os.environ.pop("OPSX_BUG_CASE_STORE_ROOT", None)
            else:
                os.environ["OPSX_BUG_CASE_STORE_ROOT"] = self.prev

    return _Ctx()


def cmd_04_identity_conflict_blocks() -> Result:
    test_id = "CMD-04"
    base = TEST_BASE / f"cmd04-{uuid.uuid4().hex[:8]}"
    fleet = base / "fleet"
    repo = _mini_repo(base, "cmd04", "https://github.com/org/cmd04.git")
    prev = os.environ.get("OPSX_PROJECT_NAME_BINDING")
    os.environ["OPSX_PROJECT_NAME_BINDING"] = "wrong-project-name"
    try:
        try:
            route_bug_team_command("new bug", repo_root=repo, acceptance_store_root=fleet)
            return False, "expected block", test_id
        except (CommandRoutingBlocked, DurableStoreUnresolved):
            return True, "fail-closed", test_id
    finally:
        if prev is None:
            os.environ.pop("OPSX_PROJECT_NAME_BINDING", None)
        else:
            os.environ["OPSX_PROJECT_NAME_BINDING"] = prev
        shutil.rmtree(base, ignore_errors=True)


def cmd_05_no_tmp_fallback() -> Result:
    test_id = "CMD-05"
    base = TEST_BASE / f"cmd05-{uuid.uuid4().hex[:8]}"
    repo = _mini_repo(base, "cmd05", "https://github.com/org/cmd05.git")
    try:
        route = route_bug_team_command(
            "intake",
            repo_root=repo,
            acceptance_store_root=base / "fleet",
        )
        legacy = legacy_repo_local_case_root(repo, route.case_id)
        ok = route.entry_class == CommandEntryClass.DURABLE_REAL_CASE and not str(route.case_root).startswith(
            str(legacy.parent)
        )
        return ok, str(route.case_root), test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def cmd_06_resume_preserves_event_identity() -> Result:
    test_id = "CMD-06"
    base = TEST_BASE / f"cmd06-{uuid.uuid4().hex[:8]}"
    fleet = base / "fleet"
    repo = _mini_repo(base, "cmd06", "https://github.com/org/cmd06.git")
    try:
        route = route_bug_team_command("intake", repo_root=repo, acceptance_store_root=fleet)
        write_settings(route.case_root, CausalHistorySettings.synthetic_v3())
        proof = route.case_root / "results" / "P.md"
        proof.parent.mkdir(parents=True, exist_ok=True)
        proof.write_text("p\n", encoding="utf-8")
        store = CausalEventStore(route.case_root, route.case_id)
        dig = hashlib.sha256(proof.read_bytes()).hexdigest()
        pub = store.publish_event(
            "WEX/t/1:CAUSAL_EVENT",
            {"EVENT_KIND": "HOP_COMPLETED"},
            EvidenceBinding("results/P.md", dig, "1:w"),
        )
        route2 = route_bug_team_command(
            f"case={route.case_id}",
            repo_root=repo,
            acceptance_store_root=fleet,
        )
        replay = CausalEventStore(route2.case_root, route2.case_id).replay_deterministic()
        ok = len(replay) == 1 and replay[0]["EVENT_ID"] == pub.event_id
        return ok, pub.event_id, test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def cmd_07_legacy_not_migrated() -> Result:
    test_id = "CMD-07"
    base = TEST_BASE / f"cmd07-{uuid.uuid4().hex[:8]}"
    fleet = base / "fleet"
    repo = _mini_repo(base, "cmd07", "https://github.com/org/cmd07.git")
    case_id = "BUGCASE-LEGACY-ONLY"
    try:
        legacy = legacy_repo_local_case_root(repo, case_id)
        legacy.mkdir(parents=True)
        (legacy / "team" / "LEGACY.txt").parent.mkdir(parents=True, exist_ok=True)
        (legacy / "team" / "LEGACY.txt").write_text("legacy\n", encoding="utf-8")
        route = route_bug_team_command(
            f"case={case_id}",
            repo_root=repo,
            acceptance_store_root=fleet,
        )
        ok = route.entry_class == CommandEntryClass.EXPLICIT_LEGACY_CASE and route.case_root == legacy
        return ok, route.entry_class.value, test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def cmd_08_synthetic_harness_intact() -> Result:
    test_id = "CMD-08"
    tmp = Path(tempfile.mkdtemp(prefix="cmd08-"))
    proof_root = REPO / ".tmp" / "opsx-bug-team-harness" / "cmd08-proof"
    proof_root.mkdir(parents=True, exist_ok=True)
    try:
        route = route_bug_team_command(
            "proof intake",
            repo_root=REPO,
            execution_mode="REVIEW_PROOF_ONLY",
            proof_root=proof_root,
            synthetic_root=tmp,
        )
        orch = open_orchestrator_for_route(route)
        ok = (
            route.entry_class == CommandEntryClass.SYNTHETIC_TEST_CASE
            and orch.case_root_mode == CaseRootMode.SYNTHETIC_TEST_CASE
        )
        return ok, str(route.case_root), test_id
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_09_writer_boundaries() -> Result:
    test_id = "CMD-09"
    base = TEST_BASE / f"cmd09-{uuid.uuid4().hex[:8]}"
    fleet = base / "fleet"
    repo = _mini_repo(base, "cmd09", "https://github.com/org/cmd09.git")
    try:
        route = route_bug_team_command("intake", repo_root=repo, acceptance_store_root=fleet)
        orch = open_orchestrator_for_route(route)
        ok = "CAUSAL_HISTORY_APPENDER" in fs_boundary.WORKER_ALLOWLISTS
        ok = ok and orch.case_root_mode == CaseRootMode.DURABLE_PROJECT_CASE
        return ok, "durable_orchestrator", test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def cmd_10_live_v3_off() -> Result:
    test_id = "CMD-10"
    base = TEST_BASE / f"cmd10-{uuid.uuid4().hex[:8]}"
    fleet = base / "fleet"
    repo = _mini_repo(base, "cmd10", "https://github.com/org/cmd10.git")
    try:
        route = route_bug_team_command("intake", repo_root=repo, acceptance_store_root=fleet)
        ok = live_v3_append_disabled(route)
        return ok, f"live_off={ok}", test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def cmd_11_readonly_retrospective() -> Result:
    test_id = "CMD-11"
    base = TEST_BASE / f"cmd11-{uuid.uuid4().hex[:8]}"
    fleet = base / "fleet"
    repo = _mini_repo(base, "cmd11", "https://github.com/org/cmd11.git")
    try:
        route = route_bug_team_command("intake", repo_root=repo, acceptance_store_root=fleet)
        write_settings(route.case_root, CausalHistorySettings.synthetic_v3())
        store = CausalEventStore(route.case_root, route.case_id)
        proof = route.case_root / "results" / "R.md"
        proof.parent.mkdir(parents=True, exist_ok=True)
        proof.write_text("r\n", encoding="utf-8")
        dig = hashlib.sha256(proof.read_bytes()).hexdigest()
        store.publish_event(
            "WEX/r/1:CAUSAL_EVENT",
            {"EVENT_KIND": "HOP_COMPLETED"},
            EvidenceBinding("results/R.md", dig, "1:w"),
        )
        mtime_before = (route.case_root / "results" / "causal" / "events").stat().st_mtime
        ro_route, events = read_durable_case_history(
            route.case_id, repo_root=repo, acceptance_store_root=fleet
        )
        mtime_after = (route.case_root / "results" / "causal" / "events").stat().st_mtime
        ok = (
            ro_route.entry_class == CommandEntryClass.READ_ONLY_HISTORY_ACCESS
            and len(events) == 1
            and mtime_before == mtime_after
        )
        return ok, f"events={len(events)}", test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def cmd_12_concurrent_same_case() -> Result:
    test_id = "CMD-12"
    base = TEST_BASE / f"cmd12b-{uuid.uuid4().hex[:8]}"
    fleet = base / "fleet"
    repo = _mini_repo(base, "cmd12", "https://github.com/org/cmd12.git")
    errors: List[str] = []
    case_id_holder: List[str] = []

    def worker():
        try:
            cid = case_id_holder[0]
            route = route_bug_team_command(
                f"case={cid}",
                repo_root=repo,
                acceptance_store_root=fleet,
            )
            proof = route.case_root / "results" / "C.md"
            proof.parent.mkdir(parents=True, exist_ok=True)
            proof.write_text("c\n", encoding="utf-8")
            st = CausalEventStore(route.case_root, route.case_id)
            dig = hashlib.sha256(proof.read_bytes()).hexdigest()
            st.publish_event(
                f"WEX/w/{uuid.uuid4().hex[:4]}:CAUSAL_EVENT",
                {"EVENT_KIND": "HOP_COMPLETED"},
                EvidenceBinding("results/C.md", dig, "1:w"),
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))

    try:
        seed = route_bug_team_command("intake", repo_root=repo, acceptance_store_root=fleet)
        case_id_holder.append(seed.case_id)
        (seed.case_root / "team").mkdir(parents=True, exist_ok=True)
        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        route = route_bug_team_command(
            f"case={seed.case_id}",
            repo_root=repo,
            acceptance_store_root=fleet,
        )
        n = len(CausalEventStore(route.case_root, route.case_id).replay_deterministic())
        ok = not errors and n == 3
        return ok, f"events={n} err={len(errors)}", test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


TESTS: List[Tuple[str, Callable[[], Result]]] = [
    ("CMD-01", cmd_01_new_real_case_durable),
    ("CMD-02", cmd_02_second_worktree_same_history),
    ("CMD-03", cmd_03_distinct_project_same_case_id),
    ("CMD-04", cmd_04_identity_conflict_blocks),
    ("CMD-05", cmd_05_no_tmp_fallback),
    ("CMD-06", cmd_06_resume_preserves_event_identity),
    ("CMD-07", cmd_07_legacy_not_migrated),
    ("CMD-08", cmd_08_synthetic_harness_intact),
    ("CMD-09", cmd_09_writer_boundaries),
    ("CMD-10", cmd_10_live_v3_off),
    ("CMD-11", cmd_11_readonly_retrospective),
    ("CMD-12", cmd_12_concurrent_same_case),
]


def main() -> int:
    head = _head()
    exec_id = f"durable-cmd@{head[:12]}"
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
                "evidence_digest": detail if ok and len(detail) == 64 else None,
            }
        )
        print(f"{tid}: {'PASS' if ok else 'FAIL'} {detail[:120]}")

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "DURABLE_COMMAND_INTEGRATION_VERIFIED" if failed == 0 else "FAILED",
        "execution_id": exec_id,
        "revision": head,
        "tests": rows,
    }
    (ARTIFACTS / "command_integration_report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
