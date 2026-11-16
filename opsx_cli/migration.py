"""Legacy project-local OPSX cutover: plan, apply, receipt and rollback hooks."""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .context import CORE_RELATIVE, Project
from .installer import _digest, _managed_payloads
from .storage import read_json, write_json


MANIFEST_SCHEMA = 1
LEGACY_ROOT = ".opsx"
LEGACY_COMMAND_GLOB = ".cursor/commands/opsx*.md"
MANIFEST_RELATIVE = "opsx/project/migration-manifest.json"


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def legacy_present(project: Project) -> bool:
    manifest = project.root / LEGACY_ROOT / "manifest.yaml"
    lock = project.root / LEGACY_ROOT / "lock.yaml"
    return manifest.is_file() or lock.is_file()


def manifest_path(project: Project) -> Path:
    return project.root / MANIFEST_RELATIVE


async def load_entries(project: Project) -> list[dict[str, Any]]:
    data = await read_json(manifest_path(project), {})
    if not data:
        return []
    entries = data.get("entries") or []
    if not isinstance(entries, list):
        raise ValueError("migration manifest entries must be a list")
    return entries


def _entry(
    *,
    legacy_path: str,
    classification: str,
    replacement: str,
    preservation: str,
    action: str,
    verification: str,
    disposition: str,
    old_digest: str | None = None,
) -> dict[str, Any]:
    return {
        "legacy_path": legacy_path,
        "classification": classification,
        "replacement": replacement,
        "preservation": preservation,
        "action": action,
        "verification": verification,
        "disposition": disposition,
        "old_digest": old_digest,
        "new_identity": replacement or None,
    }


def _classify_dot_opsx(relative: str) -> tuple[str, str, str]:
    posix = relative.replace("\\", "/")
    if posix.startswith(".opsx/project/"):
        return "PROJECT_DURABLE_DATA", f"opsx/project/legacy-dot-opsx/{posix[len('.opsx/project/'):]}", "preserve_copy"
    if posix in {".opsx/manifest.yaml", ".opsx/lock.yaml", ".opsx/projectization.yaml", ".opsx/sources.yaml"}:
        return "PROJECT_ADAPTER", f"opsx/project/legacy-dot-opsx/meta/{Path(posix).name}", "preserve_copy"
    if posix.startswith(".opsx/runs/") or posix.startswith(".opsx/registry/"):
        return "HISTORICAL_EVIDENCE", f"opsx/runs/legacy-archive/{posix}", "archive_only"
    if posix.startswith(".opsx/shared/") or posix.startswith(".opsx/adapters/") or posix.startswith(".opsx/tools/"):
        return "OBSOLETE_LEGACY", "", "archive_only"
    if posix.startswith(".opsx/import/"):
        return "HISTORICAL_EVIDENCE", f"opsx/runs/legacy-archive/{posix}", "archive_only"
    if posix.endswith("README.md") and posix.startswith(".opsx/"):
        return "HISTORICAL_EVIDENCE", f"opsx/runs/legacy-archive/{posix}", "archive_only"
    if posix in {".opsx/MERGE_RECEIPT.md", ".opsx/OPSX-COMMAND-WORKLOG.md"}:
        return "HISTORICAL_EVIDENCE", f"opsx/runs/legacy-archive/{posix}", "archive_only"
    if posix.startswith(".opsx/"):
        return "OBSOLETE_LEGACY", f"opsx/runs/legacy-archive/{posix}", "archive_only"
    return "UNRESOLVED", "", "block_until_mapped"


def build_plan(project: Project, *, core: Path | None = None) -> dict[str, Any]:
    """Read-only migration inventory; does not mutate the tree."""
    entries: list[dict[str, Any]] = []
    command_targets = set()
    if core and core.is_dir():
        command_targets = {p.name for p in (core / ".cursor/commands").glob("opsx*.md")}
    for path in sorted(project.root.glob(".cursor/commands/opsx*.md")):
        relative = path.relative_to(project.root).as_posix()
        body = path.read_bytes()
        digest = _digest(body)
        if core and core.is_dir() and path.name in command_targets:
            core_body = (core / ".cursor/commands" / path.name).read_bytes()
            same = _digest(core_body) == digest
            action = "already_core_identical" if same else "replace_with_core_managed"
            disposition = "retire_legacy_after_install" if not same else "keep_until_install_confirms"
        else:
            action = "replace_with_core_managed"
            disposition = "retire_legacy_after_install"
        entries.append(
            _entry(
                legacy_path=relative,
                classification="CORE_SHARED",
                replacement=relative,
                preservation="managed_file_digest_map",
                action=action,
                verification="opsx doctor cursor_integration",
                disposition=disposition,
                old_digest=digest,
            )
        )
    legacy_root = project.root / LEGACY_ROOT
    if legacy_root.is_dir():
        for path in sorted(legacy_root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(project.root).as_posix()
            if any(e.get("legacy_path") == relative for e in entries):
                continue
            classification, replacement, action = _classify_dot_opsx(relative)
            entries.append(
                _entry(
                    legacy_path=relative,
                    classification=classification,
                    replacement=replacement,
                    preservation="git_history" if action == "block_until_mapped" else "archive_or_copy",
                    action=action,
                    verification="manifest_review",
                    disposition="retire_after_verified" if action != "block_until_mapped" else "hold",
                )
            )
    blocking = [e for e in entries if e["action"] == "block_until_mapped"]
    collisions = [
        e for e in entries
        if e["classification"] == "CORE_SHARED" and e["action"] == "replace_with_core_managed"
    ]
    return {
        "schema_version": MANIFEST_SCHEMA,
        "generated_at": _utc(),
        "legacy_detected": legacy_present(project),
        "status": "BLOCKED" if blocking else ("READY" if legacy_present(project) or collisions else "NO_LEGACY"),
        "entry_count": len(entries),
        "blocking": blocking,
        "collisions_pending_replace": len(collisions),
        "entries": entries,
    }


def _backup_root(project: Project) -> Path:
    return project.runs / "migration-backup" / _utc()


async def apply_plan(project: Project, plan: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    """Controlled pre-install mutation: backup, archive, staged removals."""
    if plan.get("status") == "BLOCKED":
        from .context import OpsxError
        paths = ", ".join(e["legacy_path"] for e in plan.get("blocking", []))
        raise OpsxError("MIGRATION_BLOCKED", f"Unresolved legacy paths: {paths}")
    if not plan.get("legacy_detected") and not plan.get("collisions_pending_replace"):
        return {"status": "NO_LEGACY", "dry_run": dry_run}
    backup = _backup_root(project)
    applied: list[dict[str, Any]] = []
    if dry_run:
        return {"status": "PLAN_ONLY", "dry_run": True, "backup_would_be": str(backup), **plan}
    backup.mkdir(parents=True, exist_ok=True)
    for entry in plan.get("entries", []):
        source = project.root / entry["legacy_path"]
        if not source.is_file():
            continue
        action = entry["action"]
        dest = backup / entry["legacy_path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        record = {**entry, "backup": str(dest.relative_to(project.root).as_posix())}
        if action in {"replace_with_core_managed", "retire_legacy_after_install"}:
            source.unlink()
            record["removed"] = True
        elif action == "preserve_copy" and entry.get("replacement"):
            target = project.root / entry["replacement"]
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(dest, target)
            record["copied_to"] = entry["replacement"]
        elif action == "archive_only" and entry.get("replacement"):
            target = project.root / entry["replacement"]
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(dest, target)
            record["archived_to"] = entry["replacement"]
        applied.append(record)
    receipt = {
        "schema_version": 1,
        "migration_id": backup.name,
        "applied_at": _utc(),
        "backup_root": backup.relative_to(project.root).as_posix(),
        "recovery": "Restore files from backup_root; legacy .opsx remains in archive paths.",
        "applied": applied,
        "plan_status": plan.get("status"),
    }
    await write_json(project.runs / "receipts" / "migration" / f"{backup.name}.json", receipt)
    return {"status": "APPLIED", "receipt": receipt}


async def retire_legacy_tree(project: Project, *, installation_managed: dict[str, str]) -> dict[str, Any]:
    """Remove replaced legacy command files and empty .opsx shell after successful install."""
    removed: list[str] = []
    legacy = project.root / LEGACY_ROOT
    if legacy.is_dir():
        for path in sorted(legacy.rglob("*"), reverse=True):
            if path.is_file():
                rel = path.relative_to(project.root).as_posix()
                archive = project.root / "opsx/runs/legacy-archive" / rel
                if not archive.is_file():
                    archive.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, archive)
                path.unlink()
                removed.append(rel)
            elif path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass
        try:
            legacy.rmdir()
        except OSError:
            pass
    for relative in installation_managed:
        legacy_cmd = project.root / relative
        if legacy_cmd.is_file():
            digest = _digest(legacy_cmd.read_bytes())
            if installation_managed[relative] == digest:
                continue
    return {"status": "RETIRED", "removed": removed}


async def migrate_legacy(
    project: Project,
    *,
    core: Path | None = None,
    dry_run: bool = False,
    apply: bool = False,
) -> dict[str, Any]:
    plan = build_plan(project, core=core)
    if dry_run or not apply:
        return {"phase": "plan", **plan, "dry_run": not apply}
    return {"phase": "apply", **(await apply_plan(project, plan, dry_run=False))}