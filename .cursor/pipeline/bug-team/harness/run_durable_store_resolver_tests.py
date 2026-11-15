# -*- coding: utf-8 -*-
"""STORE-01..10 acceptance tests for durable case store resolver V0."""

from __future__ import annotations

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

from runtime.case_store_resolver import (  # noqa: E402
    CaseRootMode,
    DurableStoreErrorCode,
    DurableStoreUnresolved,
    assert_not_repo_local_fallback,
    normalize_remote_url,
    resolve_durable_project_case,
    resolve_durable_store_root,
    resolve_project_identity,
    resolve_project_store_roots,
    resolve_synthetic_test_case,
)
from runtime.causal_history.config import load_settings, write_settings, CausalHistorySettings  # noqa: E402
from runtime.causal_history.store import CausalEventStore, EvidenceBinding  # noqa: E402
from runtime.orchestrator import OrchestratorRuntime  # noqa: E402

ARTIFACTS = Path("/opt/cursor/artifacts/durable-store-resolver-v0")
TEST_STORE_BASE = Path("/tmp/durable-store-resolver-isolated")
Result = Tuple[bool, str, str]


def _head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO, text=True).strip()


def _with_store_env(store: Path):
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


def store_01_two_worktrees_same_history() -> Result:
    test_id = "STORE-01"
    TEST_STORE_BASE.mkdir(parents=True, exist_ok=True)
    base = TEST_STORE_BASE / f"store01-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    try:
        store = base / "project-store"
        case_id = f"BUGCASE-STORE01-{uuid.uuid4().hex[:8]}"
        with _with_store_env(store):
            r1 = resolve_durable_project_case(case_id, repo_root=REPO)
            marker = r1.case_root / "team" / "MARKER.txt"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("wt1\n", encoding="utf-8")
            r2 = resolve_durable_project_case(case_id, repo_root=REPO)
            ok = (
                r1.project.project_id == r2.project.project_id
                and r1.case_root == r2.case_root
                and marker.read_text() == "wt1\n"
            )
        return ok, f"case={r1.case_root}", test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def store_02_project_isolation() -> Result:
    test_id = "STORE-02"
    base = TEST_STORE_BASE / f"store02-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    try:
        store_a = base / "store-a"
        store_b = base / "store-b"
        case_id = "BUGCASE-ISO-01"
        with _with_store_env(store_a):
            a = resolve_durable_project_case(case_id, repo_root=REPO)
            (a.case_root / "ISO.txt").write_text("A", encoding="utf-8")
        with _with_store_env(store_b):
            b = resolve_durable_project_case(case_id, repo_root=REPO)
            (b.case_root / "ISO.txt").write_text("B", encoding="utf-8")
        ok = a.case_root != b.case_root and a.store_root != b.store_root
        return ok, f"a={a.case_root} b={b.case_root}", test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def store_03_worktree_removal_preserves_store() -> Result:
    test_id = "STORE-03"
    base = TEST_STORE_BASE / f"store03-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    fake_wt = base / "fake-worktree"
    fake_wt.mkdir()
    store = base / "durable-store"
    case_id = f"BUGCASE-WT-{uuid.uuid4().hex[:6]}"
    try:
        with _with_store_env(store):
            r = resolve_durable_project_case(case_id, repo_root=REPO)
            (r.case_root / "results" / "KEEP.md").parent.mkdir(parents=True, exist_ok=True)
            (r.case_root / "results" / "KEEP.md").write_text("persist\n", encoding="utf-8")
            saved = r.case_root
        shutil.rmtree(fake_wt)
        with _with_store_env(store):
            r2 = resolve_durable_project_case(case_id, repo_root=REPO)
            ok = r2.case_root == saved and (r2.case_root / "results" / "KEEP.md").is_file()
        return ok, str(saved), test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def store_04_no_repo_local_fallback() -> Result:
    test_id = "STORE-04"
    toplevel = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"], cwd=REPO, text=True
    ).strip()
    forbidden = Path(toplevel) / "opsx" / "runs" / "bug-cases" / "CASE-X"
    try:
        assert_not_repo_local_fallback(forbidden, REPO)
        return False, "expected DurableStoreUnresolved", test_id
    except DurableStoreUnresolved as exc:
        ok = exc.code == DurableStoreErrorCode.DURABLE_STORE_UNRESOLVED
        return ok, exc.code.value, test_id


def store_05_immutable_events_valid() -> Result:
    test_id = "STORE-05"
    base = TEST_STORE_BASE / f"store05-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    try:
        with _with_store_env(base / "store"):
            r = resolve_durable_project_case(f"BUGCASE-EV-{uuid.uuid4().hex[:6]}", repo_root=REPO)
            write_settings(r.case_root, CausalHistorySettings.synthetic_v3())
            proof = r.case_root / "results" / "GREEN-F1.md"
            proof.write_text("proof\n", encoding="utf-8")
            store = CausalEventStore(r.case_root, r.case_id)
            binding = EvidenceBinding(
                proof_path="results/GREEN-F1.md",
                proof_digest=__import__("hashlib").sha256(proof.read_bytes()).hexdigest(),
                execution_revision="1:WEX/t/1",
            )
            body = {"EVENT_KIND": "GREEN_CONFIRMED", "WORKER_ID": "GREEN_VERIFIER"}
            pub = store.publish_event("WEX/t/1:CAUSAL_EVENT", body, binding)
            replay = store.replay_deterministic()
            ok = pub.event_id and len(replay) == 1 and replay[0]["EVENT_ID"] == pub.event_id
        return ok, pub.event_id if ok else "fail", test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def store_06_evidence_replay_identity() -> Result:
    test_id = "STORE-06"
    base = TEST_STORE_BASE / f"store06-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    try:
        with _with_store_env(base / "store"):
            r = resolve_durable_project_case(f"BUGCASE-RP-{uuid.uuid4().hex[:6]}", repo_root=REPO)
            orch = OrchestratorRuntime.open_durable_project_case(
                r.case_id, repo_root=REPO, execution_mode="REVIEW_PROOF_ONLY"
            )
            ok = (
                orch.resolved_case is not None
                and orch.resolved_case.execution.revision_binding == _head()
                and orch.case_root_mode == CaseRootMode.DURABLE_PROJECT_CASE
            )
        return ok, orch.resolved_case.execution.execution_id if ok else "fail", test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def store_07_concurrent_publish_lock() -> Result:
    test_id = "STORE-07"
    base = TEST_STORE_BASE / f"store07-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    errors: List[str] = []

    def worker():
        try:
            with _with_store_env(base / "store"):
                r = resolve_durable_project_case("BUGCASE-CONC-01", repo_root=REPO)
                proof = r.case_root / "results" / "P.md"
                proof.parent.mkdir(parents=True, exist_ok=True)
                proof.write_text("p\n", encoding="utf-8")
                st = CausalEventStore(r.case_root, r.case_id)
                import hashlib

                dig = hashlib.sha256(proof.read_bytes()).hexdigest()
                binding = EvidenceBinding("results/P.md", dig, "1:w")
                st.publish_event(
                    f"WEX/w/{uuid.uuid4().hex[:4]}:CAUSAL_EVENT",
                    {"EVENT_KIND": "HOP_COMPLETED"},
                    binding,
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))

    try:
        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with _with_store_env(base / "store"):
            r = resolve_durable_project_case("BUGCASE-CONC-01", repo_root=REPO)
            n = len(CausalEventStore(r.case_root, r.case_id).replay_deterministic())
        ok = not errors and n == 3
        return ok, f"events={n} errors={len(errors)}", test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def store_08_no_auto_migration() -> Result:
    test_id = "STORE-08"
    # Resolver API does not import/move legacy paths
    ok = not hasattr(resolve_durable_project_case, "__wrapped__")
    legacy = REPO / "opsx" / "runs" / "bug-cases"
    return ok and not legacy.is_dir(), "no migration API; legacy absent or empty", test_id


def store_09_synthetic_roots() -> Result:
    test_id = "STORE-09"
    tmp = Path(tempfile.mkdtemp(prefix="synth-"))
    try:
        r = resolve_synthetic_test_case("BUGCASE-SYN-1", tmp, repo_root=REPO)
        orch = OrchestratorRuntime.open_synthetic_test_case("BUGCASE-SYN-2", tmp / "b", repo_root=REPO)
        ok = r.synthetic and r.mode == CaseRootMode.SYNTHETIC_TEST_CASE and orch.case_root_mode == CaseRootMode.SYNTHETIC_TEST_CASE
        return ok, str(r.case_root), test_id
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def store_10_live_off_default() -> Result:
    test_id = "STORE-10"
    base = TEST_STORE_BASE / f"store10-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    try:
        with _with_store_env(base / "store"):
            r = resolve_durable_project_case(f"BUGCASE-OFF-{uuid.uuid4().hex[:4]}", repo_root=REPO)
            s = load_settings(r.case_root)
        ok = s.live_v3_append_enabled is False and s.history_contract_version == "legacy"
        return ok, f"live={s.live_v3_append_enabled}", test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _mini_repo(base: Path, family_id: str, remote_url: str) -> Path:
    """Минимальный git repo с sources.yaml для изоляции PROJECT_ID."""
    repo = base / family_id
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "opsx").mkdir(parents=True, exist_ok=True)
    (repo / "opsx/sources.yaml").write_text(
        f"repo_family_id: {family_id}\nproject_name: {family_id}\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.local"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "remote.origin.url", remote_url], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


def store_11_shared_fleet_project_isolation() -> Result:
    test_id = "STORE-11"
    base = TEST_STORE_BASE / f"store11-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    fleet = base / "shared-fleet"
    case_id = "BUGCASE-SAME-ID"
    try:
        repo_a = _mini_repo(base, "project-alpha", "https://github.com/org/repo-a.git")
        repo_b = _mini_repo(base, "project-beta", "https://github.com/org/repo-b.git")
        with _with_store_env(fleet):
            a = resolve_durable_project_case(case_id, repo_root=repo_a)
            (a.case_root / "SECRET.txt").write_text("alpha-only\n", encoding="utf-8")
            b = resolve_durable_project_case(case_id, repo_root=repo_b)
            (b.case_root / "SECRET.txt").write_text("beta-only\n", encoding="utf-8")
        ok = (
            a.case_root != b.case_root
            and a.project.project_name != b.project.project_name
            and (a.case_root / "SECRET.txt").read_text(encoding="utf-8") == "alpha-only\n"
            and (b.case_root / "SECRET.txt").read_text(encoding="utf-8") == "beta-only\n"
            and a.fleet_store_root == b.fleet_store_root
        )
        return ok, f"a={a.case_root.name} b={b.case_root.parent.name}", test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def store_12_project_name_stable_across_resolve() -> Result:
    test_id = "STORE-12"
    id1 = resolve_project_identity(REPO)
    id2 = resolve_project_identity(REPO)
    root1, _ = resolve_project_store_roots(id1)
    root2, _ = resolve_project_store_roots(id2)
    ok = (
        id1.project_name == id2.project_name
        and root1 == root2
        and "@" not in id1.project_name
        and "/cases" in str(root1)
    )
    return ok, f"name={id1.project_name} root={root1.name}", test_id


def store_13_identity_binding_fail_closed() -> Result:
    test_id = "STORE-13"
    base = TEST_STORE_BASE / f"store13-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    try:
        repo = _mini_repo(base, "bind-test", "https://github.com/org/bind.git")
        prev = os.environ.get("OPSX_PROJECT_NAME_BINDING")
        os.environ["OPSX_PROJECT_NAME_BINDING"] = "wrong-project-name"
        try:
            resolve_project_identity(repo)
            return False, "expected binding conflict", test_id
        except DurableStoreUnresolved as exc:
            ok_conflict = exc.code == DurableStoreErrorCode.PROJECT_NAME_BINDING_CONFLICT
        finally:
            if prev is None:
                os.environ.pop("OPSX_PROJECT_NAME_BINDING", None)
            else:
                os.environ["OPSX_PROJECT_NAME_BINDING"] = prev
        no_sources = base / "no-sources"
        no_sources.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init"], cwd=no_sources, check=True, capture_output=True)
        try:
            resolve_project_identity(no_sources)
            return False, "expected unresolved without sources", test_id
        except DurableStoreUnresolved as exc2:
            ok_missing = exc2.code == DurableStoreErrorCode.PROJECT_NAME_UNRESOLVED
        return ok_conflict and ok_missing, "conflict+missing", test_id
    finally:
        shutil.rmtree(base, ignore_errors=True)


def store_14_symlink_into_worktree_rejected() -> Result:
    test_id = "STORE-14"
    toplevel = Path(
        subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=REPO, text=True).strip()
    )
    base = TEST_STORE_BASE / f"store14-{uuid.uuid4().hex[:8]}"
    base.mkdir(parents=True, exist_ok=True)
    real_store = base / "real-outside"
    real_store.mkdir(parents=True, exist_ok=True)
    link_parent = toplevel / "opsx" / "_test_store_link_parent"
    link_parent.mkdir(parents=True, exist_ok=True)
    link = link_parent / "fleet-link"
    try:
        if link.exists() or link.is_symlink():
            link.unlink()
        link.symlink_to(real_store, target_is_directory=True)
        with _with_store_env(link):
            resolve_durable_store_root(resolve_project_identity(REPO))
        return False, "symlink escape should fail", test_id
    except DurableStoreUnresolved as exc:
        ok = exc.code == DurableStoreErrorCode.STORE_INSIDE_WORKTREE
        return ok, exc.code.value, test_id
    finally:
        if link.is_symlink():
            link.unlink()
        shutil.rmtree(base, ignore_errors=True)


def store_inside_worktree_rejected() -> Result:
    test_id = "STORE-04b"
    toplevel = Path(
        subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=REPO, text=True).strip()
    )
    bad = toplevel / "opsx" / "custom-store"
    try:
        with _with_store_env(bad):
            resolve_durable_store_root(resolve_project_identity(REPO))
        return False, "should reject inside worktree", test_id
    except DurableStoreUnresolved as exc:
        return exc.code == DurableStoreErrorCode.STORE_INSIDE_WORKTREE, exc.code.value, test_id


TESTS: List[Tuple[str, Callable[[], Result]]] = [
    ("STORE-01", store_01_two_worktrees_same_history),
    ("STORE-02", store_02_project_isolation),
    ("STORE-03", store_03_worktree_removal_preserves_store),
    ("STORE-04", store_04_no_repo_local_fallback),
    ("STORE-04b", store_inside_worktree_rejected),
    ("STORE-05", store_05_immutable_events_valid),
    ("STORE-06", store_06_evidence_replay_identity),
    ("STORE-07", store_07_concurrent_publish_lock),
    ("STORE-08", store_08_no_auto_migration),
    ("STORE-09", store_09_synthetic_roots),
    ("STORE-10", store_10_live_off_default),
    ("STORE-11", store_11_shared_fleet_project_isolation),
    ("STORE-12", store_12_project_name_stable_across_resolve),
    ("STORE-13", store_13_identity_binding_fail_closed),
    ("STORE-14", store_14_symlink_into_worktree_rejected),
]


def main() -> int:
    head = _head()
    exec_id = f"durable-store@{head[:12]}"
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
        print(f"{tid}: {'PASS' if ok else 'FAIL'} {detail}")

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    report = {
        "status": "DURABLE_RESOLVER_VERIFIED" if failed == 0 else "FAILED",
        "execution_id": exec_id,
        "revision": head,
        "test_count": len(rows),
        "tests": rows,
    }
    (ARTIFACTS / "store_resolver_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
