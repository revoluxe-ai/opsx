"""Tests for global agent runtime file lock."""

from __future__ import annotations

import pytest

from agents.runtime_lock import OpsxAgentRuntimeLock, OpsxAgentRuntimeLockBusy


def test_runtime_lock_exclusive(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    lock_a = OpsxAgentRuntimeLock.acquire(None, owner="test_a", timeout=0)
    try:
        with pytest.raises(OpsxAgentRuntimeLockBusy):
            OpsxAgentRuntimeLock.acquire(None, owner="test_b", timeout=0)
    finally:
        lock_a.release()

    lock_b = OpsxAgentRuntimeLock.acquire(None, owner="test_b", timeout=0)
    lock_b.release()
