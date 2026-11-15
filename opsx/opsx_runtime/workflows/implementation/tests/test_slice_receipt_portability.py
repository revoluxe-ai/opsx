"""Переносимость slice receipt между Windows и Linux (пути, digest, байты JSON)."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
from unittest.mock import patch

import pytest

from workflows.implementation.routing_contract import STAGE_APPLY_RED, STAGE_APPLY_SCAFFOLD
from workflows.implementation.slice_check_workflow_receipt import (
    WORK_SCOPE_DIGEST_KEY,
    persist_work_scope_digest,
)
from workflows.implementation.slice_receipt import (
    normalize_work_path,
    validate_stage_receipt,
    write_work_fingerprints,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("src/foo.py", "src/foo.py"),
        ("src\\foo.py", "src/foo.py"),
        (".\\src\\foo.py", "src/foo.py"),
        ("./src/foo.py", "src/foo.py"),
        ("src\\nested\\bar.ts", "src/nested/bar.ts"),
        ("/etc/passwd", ""),
        ("C:\\Windows\\system32", ""),
        ("", ""),
    ],
)
def test_normalize_work_path_cross_platform(raw: str, expected: str) -> None:
    assert normalize_work_path(raw) == expected


def test_validate_accepts_mixed_separators_in_receipt_paths(tmp_path: Path) -> None:
    """Receipt с ``\\`` в путях должен совпадать с файлами, записанными через ``/``."""
    change = "portable-ch"
    slice_id = "SLICE-001"
    repo = tmp_path
    rel_posix = "tests/unit/sample_test.py"
    test_file = repo / PurePosixPath(rel_posix)
    test_file.parent.mkdir(parents=True)
    body = b"def test_sample(): assert True\n"
    _ = test_file.write_bytes(body)
    red_dir = repo / "openspec" / "changes" / change / "apply-red" / slice_id
    red_dir.mkdir(parents=True)
    receipt = {
        "status": "RED_PROVEN",
        "test_paths": ["tests\\unit\\sample_test.py"],
        "fixture_paths": [],
    }
    receipt_path = red_dir / "RED.json"
    _ = receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    result = validate_stage_receipt(change, slice_id, STAGE_APPLY_RED, repo_root=repo)
    assert result.valid, result.code
    assert result.work_paths == (rel_posix,)
    on_disk = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert on_disk.get(WORK_SCOPE_DIGEST_KEY)


def test_persist_work_scope_digest_atomic_lf(tmp_path: Path) -> None:
    """``persist_work_scope_digest``: LF, sort_keys, без CRLF."""
    change = "persist-direct"
    slice_id = "SLICE-001"
    repo = tmp_path
    rel = "pkg/item.py"
    file_path = repo / rel
    file_path.parent.mkdir(parents=True)
    _ = file_path.write_bytes(b"v = 1\n")
    scaffold_dir = repo / "openspec" / "changes" / change / "apply-scaffold" / slice_id
    scaffold_dir.mkdir(parents=True)
    receipt_path = scaffold_dir / "SCAFFOLD.json"
    receipt = {
        "status": "SCAFFOLD_READY",
        "created_paths": [rel],
        "extended_paths": [],
    }
    _ = receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    digest = "deadbeef" * 8
    payload = persist_work_scope_digest(
        change,
        slice_id,
        STAGE_APPLY_SCAFFOLD,
        digest,
        repo_root=repo,
    )
    assert payload[WORK_SCOPE_DIGEST_KEY] == digest
    from workflows.implementation.slice_receipt import stage_scope_overlay_path

    overlay_path = stage_scope_overlay_path(
        change,
        slice_id,
        STAGE_APPLY_SCAFFOLD,
        repo_root=repo,
    )
    raw = overlay_path.read_bytes()
    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")
    on_disk = json.loads(raw.decode("utf-8"))
    assert on_disk[WORK_SCOPE_DIGEST_KEY] == digest
    useful_raw = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert WORK_SCOPE_DIGEST_KEY not in useful_raw


def test_persist_work_scope_digest_retries_transient_replace_error(
    tmp_path: Path,
) -> None:
    """Первая ``os.replace`` с errno 22 — вторая попытка успешна."""
    change = "persist-retry"
    slice_id = "SLICE-001"
    repo = tmp_path
    rel = "src/retry_target.py"
    file_path = repo / rel
    file_path.parent.mkdir(parents=True)
    _ = file_path.write_bytes(b"# ok\n")
    scaffold_dir = repo / "openspec" / "changes" / change / "apply-scaffold" / slice_id
    scaffold_dir.mkdir(parents=True)
    receipt_path = scaffold_dir / "SCAFFOLD.json"
    _ = receipt_path.write_text(
        json.dumps(
            {
                "status": "SCAFFOLD_READY",
                "created_paths": [rel],
                "extended_paths": [],
            }
        ),
        encoding="utf-8",
    )
    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(22, "Invalid argument")
        real_replace(src, dst)

    with patch(
        "workflows.implementation.slice_check_workflow_receipt.os.replace",
        side_effect=flaky_replace,
    ):
        _ = persist_work_scope_digest(
            change,
            slice_id,
            STAGE_APPLY_SCAFFOLD,
            "cafebabe" * 8,
            repo_root=repo,
        )
    assert calls["n"] == 2
    raw = receipt_path.read_bytes()
    assert b"\r\n" not in raw
    assert WORK_SCOPE_DIGEST_KEY in json.loads(raw.decode("utf-8"))


def test_write_scope_digest_uses_lf_newline(tmp_path: Path) -> None:
    """Запись receipt: LF и детерминированный ``sort_keys``."""
    change = "portable-write"
    slice_id = "SLICE-001"
    repo = tmp_path
    rel = "src/module.py"
    file_path = repo / rel
    file_path.parent.mkdir(parents=True)
    _ = file_path.write_text("x = 1\n", encoding="utf-8", newline="\n")
    scaffold_dir = repo / "openspec" / "changes" / change / "apply-scaffold" / slice_id
    scaffold_dir.mkdir(parents=True)
    receipt_path = scaffold_dir / "SCAFFOLD.json"
    receipt = {
        "status": "SCAFFOLD_READY",
        "created_paths": ["src\\module.py"],
        "extended_paths": [],
    }
    _ = receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    _ = write_work_fingerprints(change, slice_id, STAGE_APPLY_SCAFFOLD, repo_root=repo)
    raw = receipt_path.read_bytes()
    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")
    on_disk = json.loads(raw.decode("utf-8"))
    assert on_disk.get(WORK_SCOPE_DIGEST_KEY)
    second = write_work_fingerprints(change, slice_id, STAGE_APPLY_SCAFFOLD, repo_root=repo)
    assert receipt_path.read_bytes() == raw
    assert second.get(WORK_SCOPE_DIGEST_KEY) == on_disk.get(WORK_SCOPE_DIGEST_KEY)


def test_migrate_accepts_backslash_path_keys(tmp_path: Path) -> None:
    """Пути с ``\\`` в receipt мигрируются в digest без ``work_file_fingerprints``."""
    change = "portable-fp"
    slice_id = "SLICE-001"
    repo = tmp_path
    rel = "lib/handler.py"
    file_path = repo / rel
    file_path.parent.mkdir(parents=True)
    content = b"# handler\n"
    _ = file_path.write_bytes(content)
    red_dir = repo / "openspec" / "changes" / change / "apply-red" / slice_id
    red_dir.mkdir(parents=True)
    receipt = {
        "status": "RED_PROVEN",
        "test_paths": [rel],
        "fixture_paths": [],
    }
    _ = (red_dir / "RED.json").write_text(json.dumps(receipt), encoding="utf-8")
    assert validate_stage_receipt(change, slice_id, STAGE_APPLY_RED, repo_root=repo).valid


def test_validate_rejects_path_outside_repo_root(tmp_path: Path) -> None:
    change = "escape-ch"
    slice_id = "SLICE-001"
    repo = tmp_path
    red_dir = repo / "openspec" / "changes" / change / "apply-red" / slice_id
    red_dir.mkdir(parents=True)
    outside = tmp_path.parent / "outside_secret.py"
    _ = outside.write_text("secret", encoding="utf-8")
    receipt = {
        "status": "RED_PROVEN",
        "test_paths": ["../outside_secret.py"],
        "fixture_paths": [],
    }
    _ = (red_dir / "RED.json").write_text(json.dumps(receipt), encoding="utf-8")
    result = validate_stage_receipt(change, slice_id, STAGE_APPLY_RED, repo_root=repo)
    assert not result.valid
    assert result.code == "FILE_MISSING"


@pytest.mark.parametrize("platform_style", ["posix", "windows"])
def test_linux_and_windows_receipt_shapes_equivalent(
    tmp_path: Path,
    platform_style: str,
) -> None:
    """Эмуляция receipt, собранных на Linux (/) и Windows (\\): один ответ проверки."""
    change = "dual-platform"
    slice_id = "SLICE-001"
    repo = tmp_path
    rel_posix = "tests/cross_platform_test.py"
    test_file = repo / PurePosixPath(rel_posix)
    test_file.parent.mkdir(parents=True)
    _ = test_file.write_bytes(b"print('ok')\n")
    rel_in_receipt = rel_posix if platform_style == "posix" else r"tests\cross_platform_test.py"
    red_dir = repo / "openspec" / "changes" / change / "apply-red" / slice_id
    red_dir.mkdir(parents=True)
    receipt = {
        "status": "RED_PROVEN",
        "test_paths": [rel_in_receipt],
        "fixture_paths": [],
    }
    _ = (red_dir / "RED.json").write_text(json.dumps(receipt), encoding="utf-8")
    validation = validate_stage_receipt(change, slice_id, STAGE_APPLY_RED, repo_root=repo)
    assert validation.valid
    assert validation.work_paths == (rel_posix,)
