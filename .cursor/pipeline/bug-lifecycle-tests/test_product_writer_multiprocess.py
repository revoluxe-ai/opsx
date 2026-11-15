"""Multiprocess contention + terminal-only reclaim for global product writer lock."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_PIPELINE = REPO_ROOT / ".cursor/pipeline"
if str(_PIPELINE) not in sys.path:
    sys.path.insert(0, str(_PIPELINE))

from bug_lifecycle import product_writer_global
from bug_lifecycle.bug_team_transport import write_execution_terminal

_CHILD = r"""
import json
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from bug_lifecycle import product_writer_global
root = Path(sys.argv[2])
out = Path(sys.argv[6])
try:
    product_writer_global.acquire(
        root,
        mission_id=sys.argv[3],
        execution_id=sys.argv[4],
        finding_id=sys.argv[5],
    )
    out.write_text(json.dumps({"ok": True}), encoding="utf-8")
except product_writer_global.ProductWriterError as error:
    out.write_text(json.dumps({"ok": False, "reason": error.reason}), encoding="utf-8")
"""


def _run_child_acquire(root: Path, mission: str, execution: str, finding: str, out: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            _CHILD,
            str(_PIPELINE),
            str(root),
            mission,
            execution,
            finding,
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_two_processes_cannot_both_hold_lock(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    product_writer_global.acquire(root, mission_id="M-hold", execution_id="exec-h", finding_id="F1")
    out = tmp_path / "child.json"
    proc = _run_child_acquire(root, "M-other", "exec-o", "F2", out)
    assert proc.returncode == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert payload["reason"] == "CROSS_MISSION_PRODUCT_WRITER"


_RECLAIM_RACE_CHILD = r"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, sys.argv[1])
root = Path(sys.argv[2])
sync = Path(sys.argv[3])
mission, execution, finding = sys.argv[4], sys.argv[5], sys.argv[6]
out = Path(sys.argv[7])
while not sync.is_file():
    time.sleep(0.005)
from bug_lifecycle import product_writer_global

try:
    product_writer_global.acquire(
        root,
        mission_id=mission,
        execution_id=execution,
        finding_id=finding,
    )
    out.write_text(json.dumps({"ok": True}), encoding="utf-8")
except product_writer_global.ProductWriterError as error:
    out.write_text(json.dumps({"ok": False, "reason": error.reason}), encoding="utf-8")
"""


def test_simultaneous_reclaim_race_only_one_wins(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    product_writer_global.acquire(root, mission_id="M1", execution_id="exec-1", finding_id="F1")
    write_execution_terminal(
        root,
        mission_id="M1",
        execution_id="exec-1",
        head="h1",
        source="HOST_BUG_TEAM",
    )
    sync = tmp_path / "go.sync"
    out_a = tmp_path / "a.json"
    out_b = tmp_path / "b.json"
    common = [
        sys.executable,
        "-c",
        _RECLAIM_RACE_CHILD,
        str(_PIPELINE),
        str(root),
        str(sync),
    ]
    proc_a = subprocess.Popen(common + ["M2", "exec-2", "F2", str(out_a)])
    proc_b = subprocess.Popen(common + ["M3", "exec-3", "F3", str(out_b)])
    time.sleep(0.05)
    sync.write_text("go", encoding="utf-8")
    assert proc_a.wait(timeout=30) == 0
    assert proc_b.wait(timeout=30) == 0
    outcomes = [
        json.loads(out_a.read_text(encoding="utf-8")),
        json.loads(out_b.read_text(encoding="utf-8")),
    ]
    winners = [o for o in outcomes if o.get("ok")]
    losers = [o for o in outcomes if not o.get("ok")]
    assert len(winners) == 1
    assert len(losers) == 1
    assert losers[0].get("reason") in {
        "LOCK_RACE_RETRY",
        "CROSS_MISSION_PRODUCT_WRITER",
        "LOCK_OWNERSHIP_AMBIGUOUS",
    }


def test_reclaim_only_after_execution_terminal_marker(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    product_writer_global.acquire(root, mission_id="M1", execution_id="exec-1", finding_id="F1")
    blocked = tmp_path / "blocked.json"
    proc = _run_child_acquire(root, "M2", "exec-2", "F2", blocked)
    assert proc.returncode == 0
    assert json.loads(blocked.read_text(encoding="utf-8"))["reason"] == "CROSS_MISSION_PRODUCT_WRITER"
    write_execution_terminal(
        root,
        mission_id="M1",
        execution_id="exec-1",
        head="h1",
        source="HOST_BUG_TEAM",
    )
    product_writer_global.acquire(root, mission_id="M2", execution_id="exec-2", finding_id="F2")
    lock = product_writer_global.read_lock(root)
    assert lock is not None
    assert lock.get("mission_id") == "M2"


_RELEASE_OVERLAP_CHILD = r"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, sys.argv[1])
root = Path(sys.argv[2])
ready = Path(sys.argv[3])
go = Path(sys.argv[4])
out = Path(sys.argv[5])
from bug_lifecycle import product_writer_global
from bug_lifecycle.bug_team_transport import write_execution_terminal

write_execution_terminal(
    root,
    mission_id="M1",
    execution_id="exec-1",
    head="h1",
    source="TEST",
)
ready.write_text("ready", encoding="utf-8")
while not go.is_file():
    time.sleep(0.005)
try:
    product_writer_global.acquire(
        root,
        mission_id="M2",
        execution_id="exec-2",
        finding_id="F2",
        head="h2",
    )
    out.write_text(json.dumps({"ok": True, "holder": "M2"}), encoding="utf-8")
except product_writer_global.ProductWriterError as error:
    out.write_text(json.dumps({"ok": False, "reason": error.reason}), encoding="utf-8")
"""


def test_release_cannot_delete_new_holder_lock(tmp_path: Path) -> None:
    """Test B: overlapping release vs acquire — release must not unlink the new holder."""
    root = tmp_path / "ws"
    root.mkdir()
    product_writer_global.acquire(
        root,
        mission_id="M1",
        execution_id="exec-1",
        finding_id="F1",
        head="h1",
    )
    write_execution_terminal(
        root,
        mission_id="M1",
        execution_id="exec-1",
        head="h1",
        source="HOST_BUG_TEAM",
    )
    ready = tmp_path / "child.ready"
    go = tmp_path / "go.sync"
    out = tmp_path / "child.json"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            _RELEASE_OVERLAP_CHILD,
            str(_PIPELINE),
            str(root),
            str(ready),
            str(go),
            str(out),
        ]
    )
    for _ in range(400):
        if ready.is_file():
            break
        time.sleep(0.01)
    assert ready.is_file()
    go.write_text("go", encoding="utf-8")
    for _ in range(400):
        if out.is_file():
            break
        time.sleep(0.01)
    assert proc.wait(timeout=30) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload.get("ok") is True
    with pytest.raises(product_writer_global.ProductWriterError) as stale_release:
        product_writer_global.release_on_terminal(
            root,
            mission_id="M1",
            execution_id="exec-1",
            execution_terminal="CONFIRMED",
            head="h1",
        )
    assert stale_release.value.reason == "LOCK_MISSION_MISMATCH"
    lock = product_writer_global.read_lock(root)
    assert lock is not None
    assert lock.get("mission_id") == "M2"


def test_reclaim_blocked_without_valid_terminal_evidence(tmp_path: Path) -> None:
    """Test D: reclaim requires matching terminal mission/execution/head/liveness."""
    root = tmp_path / "ws"
    root.mkdir()
    product_writer_global.acquire(
        root,
        mission_id="M1",
        execution_id="exec-1",
        finding_id="F1",
        head="h1",
    )
    with pytest.raises(product_writer_global.ProductWriterError) as missing:
        product_writer_global.acquire(root, mission_id="M2", execution_id="exec-2", finding_id="F2")
    assert missing.value.reason == "CROSS_MISSION_PRODUCT_WRITER"

    write_execution_terminal(
        root,
        mission_id="M1",
        execution_id="exec-WRONG",
        head="h1",
        source="HOST_BUG_TEAM",
    )
    with pytest.raises(product_writer_global.ProductWriterError) as wrong_exec:
        product_writer_global.acquire(root, mission_id="M2", execution_id="exec-2", finding_id="F2")
    assert wrong_exec.value.reason == "CROSS_MISSION_PRODUCT_WRITER"

    write_execution_terminal(
        root,
        mission_id="M1",
        execution_id="exec-1",
        head="h-wrong",
        source="HOST_BUG_TEAM",
    )
    with pytest.raises(product_writer_global.ProductWriterError) as wrong_head:
        product_writer_global.acquire(root, mission_id="M2", execution_id="exec-2", finding_id="F2")
    assert wrong_head.value.reason == "CROSS_MISSION_PRODUCT_WRITER"

    write_execution_terminal(
        root,
        mission_id="M1",
        execution_id="exec-1",
        head="h1",
        source="HOST_BUG_TEAM",
    )
    with pytest.raises(product_writer_global.ProductWriterError) as liveness:
        product_writer_global.acquire(
            root,
            mission_id="M2",
            execution_id="exec-2",
            finding_id="F2",
            worker_liveness="UNCONFIRMED",
        )
    assert liveness.value.reason == "WORKER_LIVENESS_UNCONFIRMED_NO_LOCK_ACQUIRE"


def test_subprocess_kill_during_transition_retains_single_authority(tmp_path: Path) -> None:
    """Test C: killed competitor must not leave dual writer authority."""
    root = tmp_path / "ws"
    root.mkdir()
    product_writer_global.acquire(root, mission_id="M1", execution_id="exec-1", finding_id="F1")
    write_execution_terminal(
        root,
        mission_id="M1",
        execution_id="exec-1",
        head="h1",
        source="HOST_BUG_TEAM",
    )
    sync = tmp_path / "barrier.sync"
    out_a = tmp_path / "a.json"
    out_b = tmp_path / "b.json"
    common = [
        sys.executable,
        "-c",
        _RECLAIM_RACE_CHILD,
        str(_PIPELINE),
        str(root),
        str(sync),
    ]
    proc_a = subprocess.Popen(common + ["M2", "exec-2", "F2", str(out_a)])
    time.sleep(0.05)
    proc_a.terminate()
    proc_a.wait(timeout=10)
    proc_b = subprocess.Popen(common + ["M3", "exec-3", "F3", str(out_b)])
    sync.write_text("go", encoding="utf-8")
    assert proc_b.wait(timeout=30) == 0
    payload_b = json.loads(out_b.read_text(encoding="utf-8"))
    assert payload_b.get("ok") is True
    lock = product_writer_global.read_lock(root)
    assert lock is not None
    assert lock.get("mission_id") == "M3"
    recovery = product_writer_global.recover_lock_state(root)
    assert recovery.get("STATUS") in {"LOCK_RETAINED", "NO_LOCK"}
