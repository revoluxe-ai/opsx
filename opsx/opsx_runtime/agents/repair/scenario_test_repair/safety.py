"""Read-only workspace change tracking and test-only write boundary.

The SDK tool tier is inherited from the established Apply role. A postflight
diff is detection, NOT an OS write sandbox. Live invocation must use an
explicitly authorized disposable/isolated workspace.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


class RepairSafetyError(ValueError):
    """The workspace cannot be proven safe for a repair hop."""


_SNAPSHOT_SKIP_PREFIXES = (
    "opsx/runs/dialogs/",
    "tools/langgraph_runtime/.venv",
    "tools/langgraph_runtime/.venv312",
    "tools/temporal/.venv",
    "tools/cursor_executor/.venv",
)


def _git_paths(root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            check=True, capture_output=True, timeout=25,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RepairSafetyError(f"REPAIR_GIT_SNAPSHOT_UNAVAILABLE: {type(exc).__name__}") from exc
    return sorted(set(p.decode("utf-8") for p in result.stdout.split(b"\0") if p))


def _gitlinks(root: Path) -> dict[str, str]:
    """Read index modes: Gitlink 160000 is not a regular file or submodule content."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--stage", "-z"],
            check=True, capture_output=True, timeout=25,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RepairSafetyError(f"REPAIR_GITLINK_INDEX_UNAVAILABLE: {type(exc).__name__}") from exc
    links: dict[str, str] = {}
    for row in result.stdout.split(b"\0"):
        if not row or b"\t" not in row:
            continue
        metadata, path = row.split(b"\t", 1)
        parts = metadata.split()
        if len(parts) == 3 and parts[0] == b"160000" and parts[2] == b"0":
            links[path.decode("utf-8")] = parts[1].decode("ascii")
    return links


def workspace_snapshot(root: Path) -> dict[str, str]:
    """Hash tracked and non-ignored untracked files, including preexisting dirty edits."""
    base = root.resolve()
    paths = _git_paths(base)
    gitlinks = _gitlinks(base)
    if len(paths) > 40_000:
        raise RepairSafetyError("REPAIR_SNAPSHOT_TOO_MANY_FILES")
    output: dict[str, str] = {}
    read_bytes = 0
    for rel in paths:
        if any(rel.startswith(prefix) for prefix in _SNAPSHOT_SKIP_PREFIXES):
            continue
        path = base / rel
        if rel in gitlinks:
            # Git checkout materializes a tracked submodule as an EMPTY dir,
            # even when .gitmodules is absent. Never traverse an unknown
            # nested repository or silently ignore populated gitlinks.
            if not path.exists():
                output[rel] = "<deleted>"
            elif path.is_dir() and not path.is_symlink() and not any(path.iterdir()):
                output[rel] = f"<gitlink:{gitlinks[rel]}>"
            else:
                raise RepairSafetyError(f"REPAIR_UNSAFE_GITLINK: {rel}")
            continue
        if not path.exists():
            output[rel] = "<deleted>"
            continue
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(base):
            raise RepairSafetyError(f"REPAIR_UNSAFE_PATH: {rel}")
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            while chunk := fh.read(65536):
                read_bytes += len(chunk)
                if read_bytes > 500 * 1024 * 1024:
                    raise RepairSafetyError("REPAIR_SNAPSHOT_TOO_LARGE")
                digest.update(chunk)
        output[rel] = digest.hexdigest()
    return output


def changed_paths(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))


def disallowed_paths(paths: list[str]) -> list[str]:
    """Only existing/created files inside tests/ may change."""
    return sorted(
        p for p in paths
        if not p.startswith("tests/") or p == "tests/" or ".." in Path(p).parts
    )
