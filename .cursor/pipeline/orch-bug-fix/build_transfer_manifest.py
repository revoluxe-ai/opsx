"""Build a recursive dependency manifest for /opsx-orch-bug-fix (GAME → STROY transfer).

Reads command markdown under .cursor/commands and pipeline refs; emits JSON under
opsx/transfer/candidates/<source_id>/.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import deque
from pathlib import Path

ROOT_COMMAND = "opsx-orch-bug-fix"
SEED_COMMANDS = (
    "opsx-orch-bug-fix",
    "opsx-bug-intake-clear",
    "opsx-bug-family-plan",
    "opsx-bug-disposition",
    "opsx-bug-team",
    "opsx-materialize-bug-queue",
    "opsx-evolution",
    "opsx-bug-debt-board",
    "opsx-meta-fix-command-defects",
    "opsx-orch-bugbot-converge",
    "tempo-security",
)
SEED_PATH_PREFIXES = (
    ".cursor/pipeline/orch-core/",
    ".cursor/pipeline/bug-team/",
    ".cursor/pipeline/contracts/ORCH_TARGET_RUNTIME.md",
    ".cursor/pipeline/contracts/bug-episode/",
    ".cursor/commands/_fixtures/orch-bug-fix/",
    ".cursor/pipeline/orch-bug-fix/",
    ".cursor/pipeline/orch-bug-fix-tests/",
    ".cursor/pipeline/bug_lifecycle/",
    ".cursor/pipeline/bug-lifecycle-tests/",
    ".cursor/pipeline/orch-bugbot-converge/",
    ".cursor/pipeline/orch-bugbot-converge-tests/",
    "tools/temporal/orch_target.py",
)
CMD_RE = re.compile(r"/opsx-[a-z0-9-]+")
PATH_RE = re.compile(
    r"(?:(?:\.cursor|\opsx|tools|openspec)/[A-Za-z0-9_./\-{}*]+)"
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _command_path(repo_root: Path, command_id: str) -> Path:
    return repo_root / ".cursor" / "commands" / f"{command_id}.md"


def _refs_from_text(text: str) -> tuple[set[str], set[str]]:
    commands = {token.lstrip("/") for token in CMD_RE.findall(text)}
    paths: set[str] = set()
    for match in PATH_RE.findall(text):
        cleaned = match.rstrip("`*.,)")
        if "*" in cleaned or "{" in cleaned:
            continue
        paths.add(cleaned)
    return commands, paths


def build_manifest(repo_root: Path) -> dict[str, object]:
    repo_root = repo_root.resolve()
    commands_dir = repo_root / ".cursor" / "commands"
    seen_cmds: set[str] = set()
    seen_paths: set[str] = set()
    queue: deque[str] = deque(SEED_COMMANDS)
    for prefix in SEED_PATH_PREFIXES:
        candidate = repo_root / prefix
        if candidate.is_file():
            seen_paths.add(prefix.replace("\\", "/"))
        elif candidate.is_dir():
            for path in candidate.rglob("*"):
                if path.is_file():
                    rel = path.relative_to(repo_root).as_posix()
                    seen_paths.add(rel)

    while queue:
        cmd = queue.popleft()
        if cmd in seen_cmds:
            continue
        seen_cmds.add(cmd)
        path = _command_path(repo_root, cmd)
        if not path.is_file():
            continue
        seen_paths.add(path.relative_to(repo_root).as_posix())
        text = path.read_text(encoding="utf-8", errors="replace")
        nested_cmds, nested_paths = _refs_from_text(text)
        for nested in nested_cmds:
            if nested in seen_cmds:
                continue
            if not _command_path(repo_root, nested).is_file():
                continue
            queue.append(nested)
        for rel in nested_paths:
            full = repo_root / rel
            if full.is_file():
                seen_paths.add(rel)
            elif full.is_dir():
                for child in full.rglob("*"):
                    if child.is_file():
                        seen_paths.add(child.relative_to(repo_root).as_posix())

    files: list[dict[str, str]] = []
    for rel in sorted(seen_paths):
        full = repo_root / rel
        if not full.is_file():
            continue
        files.append({"path": rel, "sha256": _sha256_file(full)})

    return {
        "schema_version": "opsx-orch-bug-fix-transfer-manifest-v1",
        "root_command": ROOT_COMMAND,
        "command_closure": sorted(seen_cmds),
        "file_count": len(files),
        "files": files,
        "seed_commands_present": sorted(
            cmd for cmd in SEED_COMMANDS if _command_path(repo_root, cmd).is_file()
        ),
        "status": "PASS",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--repo-root", type=Path, default=Path("."))
    _ = parser.add_argument(
        "--out",
        type=Path,
        default=Path("opsx/transfer/candidates/tiriel-game-65b63a8"),
    )
    _ = parser.add_argument("--source-repo", default="revoluxe-ai/tiriel-game")
    _ = parser.add_argument(
        "--source-commit",
        default="65b63a8dccd82d72e377d796ec02905dd7a50719",
    )
    args = parser.parse_args()
    repo_root = args.repo_root.resolve()
    manifest = build_manifest(repo_root)
    manifest["source_repo"] = args.source_repo
    manifest["source_commit"] = args.source_commit
    out_dir = (repo_root / args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "ORCH_BUG_FIX_DEPENDENCY_MANIFEST.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    receipt = {
        "SOURCE_COPIED": "PENDING_OWNER_VERIFY",
        "DEPENDENCIES_COMPLETE": manifest["status"],
        "manifest_path": manifest_path.relative_to(repo_root).as_posix(),
        "file_count": manifest["file_count"],
        "command_count": len(manifest["command_closure"]),
    }
    (out_dir / "TRANSFER_BUILD_RECEIPT.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"manifest": str(manifest_path), "status": manifest["status"]}))
    return 0 if manifest["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
