"""Тесты CLI collision inventory для SOURCE→TARGET command transfer."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[3]
INVENTORY = (
    Path(__file__).resolve().parents[1] / "command-transfer" / "inventory.py"
)
SOURCE_ROOT = Path(r"C:\Users\vkita\my_code\game")
TARGET_ROOT = ROOT
PINNED_SOURCE_HEAD = "91311ada29721cfb9aab7a00e1dc4d2a0a8d1982"
PINNED_TARGET_HEAD = "1a75e9a04363753cb281530db4f75f8e409492f3"
REAL_OUTPUT_REL = Path("opsx/registry/command_transfer_collision_inventory.json")


def _load_inventory() -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "command_transfer_inventory", INVENTORY
    )
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def _init_repo(repo: Path) -> None:
    _ = _git(repo, "init")
    _ = _git(repo, "config", "user.email", "inventory@test.local")
    _ = _git(repo, "config", "user.name", "inventory-test")


def _commit_all(repo: Path, message: str = "inventory-fixture") -> str:
    _ = _git(repo, "add", "-A")
    result = _git(repo, "commit", "-m", message)
    if result.returncode != 0 and "nothing to commit" not in result.stdout:
        raise AssertionError(result.stderr or result.stdout)
    head = _git(repo, "rev-parse", "HEAD")
    assert head.returncode == 0, head.stderr
    return head.stdout.strip().lower()


def _write_command(repo: Path, command_id: str, body: bytes) -> None:
    commands = repo / ".cursor" / "commands"
    commands.mkdir(parents=True, exist_ok=True)
    path = commands / f"{command_id}.md"
    path.write_bytes(body)


def _write_manifest(repo: Path, rows: list[tuple[str, str, str]]) -> None:
    header = """# manifest
| Command | State | Concrete dependency / remaining gap |
|---|---|---|
"""
    lines = [
        f"| `{command_id}` | `{state}` | {note} |"
        for command_id, state, note in rows
    ]
    path = repo / "opsx" / "registry" / "command_transfer_manifest.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")


def _run_cli(
    *,
    source: Path,
    target: Path,
    output: Path,
    source_head: str,
    target_head: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(INVENTORY),
            "--source",
            str(source),
            "--target",
            str(target),
            "--expect-source-head",
            source_head,
            "--expect-target-head",
            target_head,
            "--output",
            str(output),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def _synthetic_hundred_rows() -> list[tuple[str, str, str]]:
    return [
        (f"opsx-cmd-{index:03d}", "NOT_STARTED", "synthetic")
        for index in range(100)
    ]


@pytest.mark.unit
def test_stale_source_head_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    _init_repo(source)
    _init_repo(target)
    _write_command(source, "opsx-alpha", b"source-body")
    _write_command(target, "opsx-alpha", b"target-body")
    for index in range(99):
        _write_command(source, f"opsx-cmd-{index:03d}", b"x")
        _write_command(target, f"opsx-cmd-{index:03d}", b"y")
    _write_manifest(target, _synthetic_hundred_rows())
    target_head = _commit_all(target)
    _ = _commit_all(source)
    result = _run_cli(
        source=source,
        target=target,
        output=Path("opsx/out.json"),
        source_head="0" * 40,
        target_head=target_head,
    )
    assert result.returncode != 0
    assert "stale source HEAD" in result.stderr


@pytest.mark.unit
def test_output_outside_target_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    outside = tmp_path / "outside.json"
    source.mkdir()
    target.mkdir()
    _init_repo(source)
    _init_repo(target)
    for index in range(100):
        _write_command(source, f"opsx-cmd-{index:03d}", b"same")
        _write_command(target, f"opsx-cmd-{index:03d}", b"same")
    _write_manifest(target, _synthetic_hundred_rows())
    target_head = _commit_all(target)
    source_head = _commit_all(source)
    result = _run_cli(
        source=source,
        target=target,
        output=outside,
        source_head=source_head,
        target_head=target_head,
    )
    assert result.returncode != 0
    assert "output path" in result.stderr.lower()


@pytest.mark.unit
def test_symlink_escape_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    secret = tmp_path / "secret.md"
    source.mkdir()
    target.mkdir()
    secret.write_bytes(b"secret-bytes")
    _init_repo(source)
    _init_repo(target)
    for index in range(99):
        _write_command(source, f"opsx-cmd-{index:03d}", b"a")
        _write_command(target, f"opsx-cmd-{index:03d}", b"a")
    commands = source / ".cursor" / "commands"
    link = commands / "opsx-cmd-099.md"
    try:
        link.symlink_to(secret)
    except OSError:
        pytest.skip("symlink creation unavailable on this host")
    _write_manifest(target, _synthetic_hundred_rows())
    target_head = _commit_all(target)
    source_head = _commit_all(source)
    result = _run_cli(
        source=source,
        target=target,
        output=Path("opsx/out.json"),
        source_head=source_head,
        target_head=target_head,
    )
    assert result.returncode != 0
    assert "escapes" in result.stderr.lower()


@pytest.mark.unit
def test_synthetic_counts_and_replay(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    _init_repo(source)
    _init_repo(target)

    exact_id = "opsx-cmd-000"
    diff_id = "opsx-cmd-001"
    missing_id = "opsx-cmd-002"
    _write_command(source, exact_id, b"exact-bytes")
    _write_command(target, exact_id, b"exact-bytes")
    _write_command(source, diff_id, b"source-variant")
    _write_command(target, diff_id, b"target-variant")
    _write_command(source, missing_id, b"missing")
    for index in range(3, 100):
        command_id = f"opsx-cmd-{index:03d}"
        _write_command(source, command_id, f"body-{index}".encode())
        _write_command(target, command_id, f"body-{index}".encode())
    _write_command(target, "opsx-target-only", b"target-local")

    rows = _synthetic_hundred_rows()
    _write_manifest(target, rows)

    target_head = _commit_all(target)
    source_head = _commit_all(source)
    output_rel = Path("opsx/registry/collision.json")

    first = _run_cli(
        source=source,
        target=target,
        output=output_rel,
        source_head=source_head,
        target_head=target_head,
    )
    assert first.returncode == 0, first.stderr
    output_path = target / output_rel
    first_bytes = output_path.read_bytes()
    first_payload = json.loads(first_bytes.decode("utf-8"))
    assert first_payload["counts"] == {
        "source_commands": 100,
        "same_name_exact_bytes": 98,
        "same_name_differing_bytes": 1,
        "source_missing_in_target": 1,
        "target_only_commands": 1,
    }

    second = _run_cli(
        source=source,
        target=target,
        output=output_rel,
        source_head=source_head,
        target_head=target_head,
    )
    assert second.returncode == 0, second.stderr
    second_bytes = output_path.read_bytes()
    second_payload = json.loads(second_bytes.decode("utf-8"))
    assert second_payload["counts"] == first_payload["counts"]
    assert {row["command_id"] for row in second_payload["source_commands"]} == {
        row["command_id"] for row in first_payload["source_commands"]
    }

    def _stable_view(payload: dict[str, object]) -> str:
        trimmed = {
            "counts": payload["counts"],
            "source_commands": [
                {
                    "command_id": row["command_id"],
                    "byte_relation": row["byte_relation"],
                    "source_sha256": row["source_sha256"],
                    "target": row["target"],
                }
                for row in payload["source_commands"]
            ],
            "target_only": payload["target_only"],
        }
        return json.dumps(trimmed, sort_keys=True)

    assert hashlib.sha256(_stable_view(first_payload).encode()).hexdigest() == (
        hashlib.sha256(_stable_view(second_payload).encode()).hexdigest()
    )


@pytest.mark.unit
def test_real_pinned_repos_inventory() -> None:
    if not SOURCE_ROOT.is_dir():
        pytest.skip("SOURCE game tree is unavailable")
    actual_target_head = _git(TARGET_ROOT, "rev-parse", "HEAD").stdout.strip()
    if actual_target_head != PINNED_TARGET_HEAD:
        pytest.skip(
            "TARGET is no longer at the pinned pre-transfer baseline "
            f"{PINNED_TARGET_HEAD}; actual={actual_target_head}"
        )
    module = _load_inventory()
    output_path = TARGET_ROOT / REAL_OUTPUT_REL
    if output_path.exists():
        before = output_path.read_bytes()
    else:
        before = None

    payload = module.run_inventory(
        source=SOURCE_ROOT,
        target=TARGET_ROOT,
        expect_source_head=PINNED_SOURCE_HEAD,
        expect_target_head=PINNED_TARGET_HEAD,
        output=REAL_OUTPUT_REL,
    )

    assert output_path.is_file()
    on_disk = json.loads(output_path.read_text(encoding="utf-8"))
    assert on_disk["counts"] == payload["counts"]
    assert on_disk["schema_version"] == "command_transfer_collision_inventory_v1"
    assert payload["counts"]["source_commands"] == 100
    assert (
        payload["counts"]["same_name_exact_bytes"]
        + payload["counts"]["same_name_differing_bytes"]
        + payload["counts"]["source_missing_in_target"]
        == 100
    )
    assert payload["counts"]["same_name_differing_bytes"] == 100
    assert payload["counts"]["same_name_exact_bytes"] == 0
    assert payload["counts"]["source_missing_in_target"] == 0
    assert payload["counts"]["target_only_commands"] == 12
    state_counts = {
        state: sum(
            1
            for row in payload["source_commands"]
            if row["byte_relation"] == "differing"
            and row["manifest_state"] == state
        )
        for state in {
            "EXISTING_ADAPTATION_REQUIRED",
            "IMPLEMENTED_UNVERIFIED",
            "SOURCE_STAGED_MIGRATION_HOLD",
        }
    }
    assert state_counts == {
        "EXISTING_ADAPTATION_REQUIRED": 61,
        "IMPLEMENTED_UNVERIFIED": 23,
        "SOURCE_STAGED_MIGRATION_HOLD": 16,
    }
    for row in on_disk["source_commands"]:
        assert "manifest_state" in row
        assert row["byte_relation"] in {
            "exact",
            "differing",
            "missing_in_target",
        }
    if before is not None:
        assert before != output_path.read_bytes()
