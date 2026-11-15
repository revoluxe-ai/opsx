"""Hermetic tests for repo-scoped Temporal worker lifecycle (no real taskkill)."""

from __future__ import annotations

from pathlib import Path

import tempo_operator as op
from control_plane_code_identity import compute_control_plane_code_identity


def test_list_worker_pids_empty_when_no_repo_match(monkeypatch, tmp_path: Path):
    repo = tmp_path / "stroy-system"
    repo.mkdir()
    other = tmp_path / "other-project"
    other.mkdir()
    foreign_cmd = (
        f"python worker.py --mode real --repo {other.as_posix()} "
        "--model-config /tmp/overlay.json"
    )

    monkeypatch.setattr(op, "_wmic_workers", lambda: [(111, foreign_cmd)])

    assert op.list_worker_pids(repo=repo) == []


def test_list_worker_pids_returns_only_attested_repo(monkeypatch, tmp_path: Path):
    repo = tmp_path / "stroy-system"
    repo.mkdir()
    other = tmp_path / "other-project"
    other.mkdir()
    own_cmd = (
        f"python worker.py --mode real --repo {repo.as_posix()} "
        "--model-config /tmp/overlay.json"
    )
    foreign_cmd = (
        f"python worker.py --mode real --repo {other.as_posix()} "
        "--model-config /tmp/overlay.json"
    )

    monkeypatch.setattr(
        op,
        "_wmic_workers",
        lambda: [(1, own_cmd), (2, foreign_cmd)],
    )

    rows = op.list_worker_pids(repo=repo)
    assert rows == [(1, own_cmd)]


def test_kill_workers_never_targets_foreign_repo(monkeypatch, tmp_path: Path):
    repo = tmp_path / "stroy-system"
    repo.mkdir()
    other = tmp_path / "other-project"
    other.mkdir()
    killed: list[int] = []

    def _fake_taskkill(args, **_kwargs):
        if len(args) >= 3 and args[1] == "/PID":
            killed.append(int(args[2]))
        return None

    monkeypatch.setattr(op, "_wmic_workers", lambda: [(99, f"worker.py --repo {other.as_posix()}")])
    monkeypatch.setattr(op.subprocess, "run", _fake_taskkill)

    result = op.kill_workers(repo=repo)
    assert result == []
    assert killed == []


def test_kill_workers_only_attested_repo(monkeypatch, tmp_path: Path):
    repo = tmp_path / "stroy-system"
    repo.mkdir()
    killed: list[int] = []

    def _fake_taskkill(args, **_kwargs):
        if len(args) >= 3 and args[1] == "/PID":
            killed.append(int(args[2]))
        return None

    own_cmd = f"python worker.py --repo {repo.as_posix()}"
    monkeypatch.setattr(op, "_wmic_workers", lambda: [(42, own_cmd)])
    monkeypatch.setattr(op.subprocess, "run", _fake_taskkill)

    assert op.kill_workers(repo=repo) == [42]
    assert killed == [42]


def test_ensure_worker_multiple_repo_workers_pause_without_kill(monkeypatch, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    overlay = tmp_path / "overlay.json"
    overlay.write_text("{}", encoding="utf-8")
    cmd = f"python worker.py --repo {repo.as_posix()}"

    monkeypatch.setattr(op, "list_worker_pids", lambda **_: [(1, cmd), (2, cmd)])

    def _forbidden_kill(**_kwargs):
        raise AssertionError("kill_workers must not run from ensure_worker")

    monkeypatch.setattr(op, "kill_workers", _forbidden_kill)
    outcome = op.ensure_worker(repo, overlay, review="low", repair="low")
    assert outcome.get("action") == "pause"
    assert outcome.get("status") == "WORKER_AMBIGUOUS_MULTIPLE"


def test_ensure_worker_starts_without_killing_foreign_workers(monkeypatch, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    overlay = tmp_path / "overlay.json"
    overlay.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(op, "list_worker_pids", lambda **_: [])

    def _forbidden_kill(**_kwargs):
        raise AssertionError("kill_workers must not run when starting fresh worker")

    monkeypatch.setattr(op, "kill_workers", _forbidden_kill)
    monkeypatch.setattr(op, "_worker_listening", lambda _pid: True)

    class _Proc:
        pid = 55555

        def poll(self):
            return None

    monkeypatch.setattr(op.subprocess, "Popen", lambda *_a, **_k: _Proc())

    outcome = op.ensure_worker(repo, overlay, review="low", repair="low")
    assert outcome.get("action") == "started"
    assert outcome.get("pid") == 55555
    state = op._read_worker_state(repo)
    assert state is not None
    assert state.get("control_plane_code_identity") == compute_control_plane_code_identity()


def test_ensure_worker_config_mismatch_pauses(monkeypatch, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    overlay = tmp_path / "overlay.json"
    overlay.write_text("{}", encoding="utf-8")
    op._write_worker_state(
        repo,
        review="high",
        repair="high",
        pid=9001,
        overlay=overlay,
        control_plane_code_identity=compute_control_plane_code_identity(),
    )
    cmd = f"python worker.py --repo {repo.as_posix()}"

    monkeypatch.setattr(op, "list_worker_pids", lambda **_: [(9001, cmd)])
    monkeypatch.setattr(op, "_worker_listening", lambda _pid: True)

    outcome = op.ensure_worker(repo, overlay, review="low", repair="low")
    assert outcome.get("status") == "WORKER_CONFIG_MISMATCH"
    assert outcome.get("action") == "pause"
