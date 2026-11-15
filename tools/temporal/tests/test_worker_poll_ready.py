"""Тесты маркера poll-ready для admission worker."""

from __future__ import annotations

from worker_poll_ready import (
    clear_poll_ready,
    read_poll_ready,
    worker_is_polling,
    write_poll_ready,
)


def test_poll_ready_roundtrip(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    write_poll_ready(
        repo,
        pid=4242,
        task_queue="cursor-bugbot-repair-poc",
        control_plane_code_identity="a" * 64,
    )
    assert worker_is_polling(4242, repo)
    assert not worker_is_polling(4243, repo)
    data = read_poll_ready(repo)
    assert data is not None
    assert data["task_queue"] == "cursor-bugbot-repair-poc"
    clear_poll_ready(repo)
    assert read_poll_ready(repo) is None
    assert not worker_is_polling(4242, repo)
