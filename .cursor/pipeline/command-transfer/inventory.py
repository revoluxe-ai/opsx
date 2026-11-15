"""Machine-checkable collision inventory for SOURCE→TARGET OPSX command transfer.

Сканирует только прямые ``.cursor/commands/*.md``, хеширует точные байты,
сопоставляет с ledger ``command_transfer_manifest.md`` и activation registry.
Не пишет в SOURCE, не выводит семантическую совместимость и не повышает
``FUNCTIONAL_PROVEN``.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

SCHEMA_VERSION = "command_transfer_collision_inventory_v1"
MANIFEST_REL = Path("opsx/registry/command_transfer_manifest.md")
ACTIVATION_REL = Path("opsx/registry/command_activation.ndjson")
COMMANDS_REL = Path(".cursor/commands")


class InventoryError(ValueError):
    """Невалидные пути, устаревший HEAD или нарушение границ записи."""


def _fail(message: str) -> NoReturn:
    raise InventoryError(message)


def _normalize_sha(value: str, label: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 40 or not all(ch in "0123456789abcdef" for ch in normalized):
        _fail(f"{label} must be a 40-character lowercase git SHA")
    return normalized


def _git_head(repo: Path) -> str:
    if not (repo / ".git").exists():
        _fail(f"not a git repository: {repo}")
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        _fail(f"git rev-parse failed for {repo}: {result.stderr.strip()}")
    return result.stdout.strip().lower()


def _assert_contained(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve()
    root_resolved = root.resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError:
        _fail(f"{label} escapes repository root: {path}")
    return resolved


def _commands_dir(repo: Path) -> Path:
    commands = repo / COMMANDS_REL
    if not commands.is_dir():
        _fail(f"missing commands directory: {commands}")
    if commands.is_symlink():
        _fail(f"commands directory must not be a symlink: {commands}")
    return _assert_contained(commands.resolve(), repo.resolve(), "commands directory")


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_command_file(path: Path, repo: Path) -> bytes:
    if path.is_symlink():
        resolved = path.resolve()
        _assert_contained(resolved, repo.resolve(), f"symlink target for {path.name}")
        if not resolved.is_file():
            _fail(f"symlink does not resolve to a file: {path}")
        read_path = resolved
    else:
        _assert_contained(path, _commands_dir(repo), f"command path {path.name}")
        read_path = path
    return read_path.read_bytes()


def _list_command_files(repo: Path) -> dict[str, Path]:
    commands_dir = _commands_dir(repo)
    mapping: dict[str, Path] = {}
    for entry in sorted(commands_dir.glob("*.md")):
        if not entry.is_file() and not entry.is_symlink():
            continue
        name = entry.name
        if name in mapping:
            _fail(f"duplicate command filename: {name}")
        mapping[name] = entry
    return mapping


def _parse_manifest(target: Path) -> dict[str, dict[str, str]]:
    manifest_path = target / MANIFEST_REL
    if not manifest_path.is_file():
        _fail(f"missing transfer manifest: {manifest_path}")
    body = manifest_path.read_text(encoding="utf-8")
    rows = re.findall(
        r"^\| `([^`]+)` \| `([^`]+)` \| (.+) \|$",
        body,
        flags=re.MULTILINE,
    )
    if len(rows) != 100:
        _fail(f"transfer manifest must contain exactly 100 rows, got {len(rows)}")
    states: dict[str, dict[str, str]] = {}
    for command_id, state, note in rows:
        if command_id in states:
            _fail(f"duplicate manifest row for {command_id}")
        states[command_id] = {
            "manifest_state": state,
            "manifest_note": note.strip(),
        }
    return states


def _parse_activation(target: Path) -> dict[str, dict[str, Any]]:
    activation_path = target / ACTIVATION_REL
    if not activation_path.is_file():
        return {}
    rows: dict[str, dict[str, Any]] = {}
    for line_number, raw_line in enumerate(
        activation_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw_line.strip():
            continue
        try:
            value = json.loads(raw_line)
        except json.JSONDecodeError as error:
            _fail(f"{activation_path}:{line_number}: invalid JSON: {error}")
        if not isinstance(value, dict):
            _fail(f"{activation_path}:{line_number}: row is not an object")
        command_id = value.get("command_id")
        if not isinstance(command_id, str) or not command_id:
            _fail(f"{activation_path}:{line_number}: missing command_id")
        rows[command_id] = value
    return rows


def _resolve_output_path(target: Path, output: Path) -> Path:
    target_root = target.resolve()
    if output.is_absolute():
        resolved = output.resolve()
    else:
        resolved = (target_root / output).resolve()
    _assert_contained(resolved, target_root, "output path")
    return resolved


def _replace_file_atomically(source: Path, target: Path) -> None:
    try:
        os.replace(source, target)
        return
    except PermissionError:
        if os.name != "nt" or not target.exists():
            raise

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    replace_file = kernel32.ReplaceFileW
    replace_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    replace_file.restype = ctypes.c_int
    if not replace_file(str(target), str(source), None, 0, None, None):
        error = ctypes.get_last_error()
        raise PermissionError(error, f"ReplaceFileW failed for {target}")


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    serialized_bytes = (serialized + "\n").encode("utf-8")
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".collision_inventory.",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as stream:
            _ = stream.write(serialized_bytes)
            stream.flush()
            os.fsync(stream.fileno())
        _replace_file_atomically(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def build_inventory(
    *,
    source: Path,
    target: Path,
    expect_source_head: str,
    expect_target_head: str,
) -> dict[str, Any]:
    """Собрать полный inventory без записи на диск."""

    source_root = source.resolve()
    target_root = target.resolve()
    expected_source = _normalize_sha(expect_source_head, "expect-source-head")
    expected_target = _normalize_sha(expect_target_head, "expect-target-head")

    actual_source = _git_head(source_root)
    actual_target = _git_head(target_root)
    if actual_source != expected_source:
        _fail(
            f"stale source HEAD: expected {expected_source}, actual {actual_source}"
        )
    if actual_target != expected_target:
        _fail(
            f"stale target HEAD: expected {expected_target}, actual {actual_target}"
        )

    source_files = _list_command_files(source_root)
    target_files = _list_command_files(target_root)
    manifest = _parse_manifest(target_root)
    activation = _parse_activation(target_root)

    source_command_ids = sorted(
        {path.stem for path in source_files.values()},
        key=str.casefold,
    )
    if len(source_command_ids) != 100:
        _fail(f"SOURCE must expose exactly 100 commands, got {len(source_command_ids)}")

    missing_manifest = sorted(
        command_id for command_id in source_command_ids if command_id not in manifest
    )
    if missing_manifest:
        _fail(f"manifest missing SOURCE commands: {', '.join(missing_manifest)}")

    counts = {
        "source_commands": 0,
        "same_name_exact_bytes": 0,
        "same_name_differing_bytes": 0,
        "source_missing_in_target": 0,
        "target_only_commands": 0,
    }
    source_rows: list[dict[str, Any]] = []

    for command_id in source_command_ids:
        filename = f"{command_id}.md"
        source_path = source_files[filename]
        source_bytes = _read_command_file(source_path, source_root)
        source_sha = _hash_bytes(source_bytes)

        target_entry = target_files.get(filename)
        target_present = target_entry is not None
        target_sha: str | None = None
        byte_relation: str

        if not target_present:
            byte_relation = "missing_in_target"
            counts["source_missing_in_target"] += 1
        else:
            target_bytes = _read_command_file(target_entry, target_root)
            target_sha = _hash_bytes(target_bytes)
            if target_sha == source_sha:
                byte_relation = "exact"
                counts["same_name_exact_bytes"] += 1
            else:
                byte_relation = "differing"
                counts["same_name_differing_bytes"] += 1

        activation_row = activation.get(command_id)
        manifest_row = manifest[command_id]
        source_rows.append(
            {
                "command_id": command_id,
                "filename": filename,
                "source_sha256": source_sha,
                "target": {
                    "present": target_present,
                    "sha256": target_sha,
                },
                "byte_relation": byte_relation,
                "manifest_state": manifest_row["manifest_state"],
                "manifest_note": manifest_row["manifest_note"],
                "activation": activation_row,
            }
        )
        counts["source_commands"] += 1

    target_only: list[dict[str, Any]] = []
    source_names = set(source_files)
    for filename in sorted(target_files, key=str.casefold):
        if filename in source_names:
            continue
        command_id = Path(filename).stem
        target_bytes = _read_command_file(target_files[filename], target_root)
        target_only.append(
            {
                "command_id": command_id,
                "filename": filename,
                "target_sha256": _hash_bytes(target_bytes),
            }
        )
        counts["target_only_commands"] += 1

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "source": {
            "path": str(source_root),
            "head_expected": expected_source,
            "head_actual": actual_source,
            "command_count": len(source_command_ids),
        },
        "target": {
            "path": str(target_root),
            "head_expected": expected_target,
            "head_actual": actual_target,
            "manifest_path": str((target_root / MANIFEST_REL).resolve()),
            "activation_path": str((target_root / ACTIVATION_REL).resolve()),
        },
        "counts": counts,
        "source_commands": source_rows,
        "target_only": target_only,
    }


def run_inventory(
    *,
    source: Path,
    target: Path,
    expect_source_head: str,
    expect_target_head: str,
    output: Path,
) -> dict[str, Any]:
    """Собрать inventory и атомарно записать JSON только внутри TARGET."""

    target_root = target.resolve()
    output_path = _resolve_output_path(target_root, output)
    payload = build_inventory(
        source=source,
        target=target,
        expect_source_head=expect_source_head,
        expect_target_head=expect_target_head,
    )
    _atomic_write_json(output_path, payload)
    payload["output_path"] = str(output_path)
    return payload


def build_parser() -> argparse.ArgumentParser:
    """Построить CLI для детерминированного inventory run."""

    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--source", type=Path, required=True)
    _ = parser.add_argument("--target", type=Path, required=True)
    _ = parser.add_argument("--expect-source-head", required=True)
    _ = parser.add_argument("--expect-target-head", required=True)
    _ = parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    """Точка входа CLI: один атомарный inventory run."""

    args = build_parser().parse_args()
    payload = run_inventory(
        source=args.source,
        target=args.target,
        expect_source_head=args.expect_source_head,
        expect_target_head=args.expect_target_head,
        output=args.output,
    )
    print(
        json.dumps(
            {
                "status": "OK",
                "output_path": payload["output_path"],
                "counts": payload["counts"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except InventoryError as error:
        raise SystemExit(f"COMMAND_TRANSFER_INVENTORY_ERROR: {error}") from error
