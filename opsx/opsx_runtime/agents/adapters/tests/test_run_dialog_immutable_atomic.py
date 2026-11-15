"""Atomic write-once dialog publication under concurrent SDK completions."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from threading import Barrier

import pytest

from run_dialog_immutable import (
    RunDialogImmutableViolation,
    write_immutable_bytes,
)


def test_concurrent_distinct_writers_preserve_canonical_and_all_revisions(tmp_path):
    canonical = tmp_path / "conversation.json"
    writers = 8
    gate = Barrier(writers)
    payloads = [f'{{"sdk_run_id":"run-{i}"}}'.encode() for i in range(writers)]

    def publish(index):
        gate.wait(timeout=10)
        return write_immutable_bytes(
            canonical, payloads[index], revision_id=f"run-{index}",
        )

    with ThreadPoolExecutor(max_workers=writers) as pool:
        results = list(pool.map(publish, range(writers)))

    locations = [path for _, path in results]
    assert len(set(locations)) == writers
    assert locations.count(canonical) == 1
    assert sum(path.parent.name.startswith("run-") for path in locations) == writers - 1
    for index, (digest, location) in enumerate(results):
        assert location.read_bytes() == payloads[index]
        assert digest == sha256(payloads[index]).hexdigest()
    assert not list(tmp_path.rglob("*.pending"))


def test_concurrent_identical_content_is_idempotent(tmp_path):
    canonical = tmp_path / "meta.json"
    payload = b"immutable"
    writers = 8
    gate = Barrier(writers)

    def publish(index):
        gate.wait(timeout=10)
        return write_immutable_bytes(canonical, payload, revision_id=f"run-{index}")

    with ThreadPoolExecutor(max_workers=writers) as pool:
        results = list(pool.map(publish, range(writers)))

    assert all(location == canonical for _, location in results)
    assert canonical.read_bytes() == payload
    assert not (tmp_path / "revisions").exists()


def test_revision_identity_collision_never_replaces_first_payload(tmp_path):
    canonical = tmp_path / "conversation.md"
    _, first = write_immutable_bytes(canonical, b"first", revision_id="run-0")
    _, revision = write_immutable_bytes(canonical, b"second", revision_id="run-1")
    assert first == canonical
    assert revision.read_bytes() == b"second"

    with pytest.raises(RunDialogImmutableViolation, match="RUN_DIALOG_REVISION_CONFLICT"):
        write_immutable_bytes(canonical, b"third", revision_id="run-1")

    assert canonical.read_bytes() == b"first"
    assert revision.read_bytes() == b"second"
