"""Legacy project-local OPSX cutover: plan, apply, receipt and rollback hooks."""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .command_reconcile import build_command_matrix
from .context import CORE_RELATIVE, Project
from .installer import _digest
from .storage import read_json, write_json


MANIFEST_SCHEMA = 2
LEGACY_ROOT = ".opsx"
MANIFEST_RELATIVE = "opsx/project/migration-manifest.json"
RECOVERY_RELATIVE = "opsx/project/migration-recovery.json"
MATRIX_RELATIVE = "opsx/project/command-reconciliation.json"
LEGACY_EVIDENCE = "opsx/project/legacy-evidence"
PENDING_PLAN = "opsx/runs/migration-pending/plan.json"


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _plan_fingerprint(plan: dict[str, Any]) -> str:
    payload = json.dumps(
        {"schema_version": plan.get("schema_version"), "entries": plan.get("entries", [])},
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def legacy_present(project: Project) -> bool:
    manifest = project.root / LEGACY_ROOT / "manifest.yaml"
    lock = project.root / LEGACY_ROOT / "lock.yaml"
    return manifest.is_file() or lock.is_file()


def manifest_path(project: Project) -> Path:
    return project.root / MANIFEST_RELATIVE


def _latest_backup_commands(project: Project) -> Path | None:
    root = project.runs / "migration-backup"
    if not root.is_dir():
        return None
    backups = sorted([p for p in root.iterdir() if p.is_dir()], reverse=True)
    for backup in backups:
        commands = backup / ".cursor/commands"
        if commands.is_dir():
            return commands
    return None


def _classify_dot_opsx(relative: str) -> tuple[str, str, str]:
    posix = relative.replace("\\", "/")
    if posix.startswith(".opsx/project/"):
        return "PROJECT_DURABLE_DATA", f"{LEGACY_EVIDENCE}/dot-opsx-project/{posix[len('.opsx/project/'):]}", "preserve_copy"
    if posix in {".opsx/manifest.yaml", ".opsx/lock.yaml", ".opsx/projectization.yaml", ".opsx/sources.yaml"}:
        return "PROJECT_ADAPTER", f"{LEGACY_EVIDENCE}/meta/{Path(posix).name}", "preserve_copy"
    if posix.startswith(".opsx/"):
        return "HISTORICAL_EVIDENCE", f"{LEGACY_EVIDENCE}/archive-index/{posix.replace('/', '__')}.json", "archive_only"
    return "UNRESOLVED", "", "block_until_mapped"


def build_plan(project: Project, *, core: Path | None = None) -> dict[str, Any]:
    """Read-only migration inventory with command semantic reconciliation."""
    entries: list[dict[str, Any]] = []
    waivers = {}
    matrix_path = project.root / MATRIX_RELATIVE
    if matrix_path.is_file():
        waivers = {
            row["command"]: row.get("replacement_decision")
            for row in json.loads(matrix_path.read_text(encoding="utf-8")).get("commands", [])
            if row.get("replacement_decision") == "CORE_CANONICAL_WAIVER"
        }
    legacy_commands = project.root / ".cursor/commands"
    if not any(legacy_commands.glob("opsx*.md")):
        legacy_commands = _latest_backup_commands(project) or legacy_commands
    matrix = build_command_matrix(project.root, core=core or project.core,
                                  legacy_commands_root=legacy_commands, waivers=waivers)
    for row in matrix.get("commands", []):
        name = row["command"]
        relative = f".cursor/commands/{name}"
        action = row.get("migration_action", "block_until_proven")
        entries.append({
            "legacy_path": relative,
            "classification": "CORE_SHARED",
            "replacement": relative,
            "preservation": row.get("preservation", "managed_file_digest_map"),
            "action": action,
            "verification": row.get("verification", "semantic_fingerprint"),
            "disposition": "retire_after_install" if action == "replace_with_core_managed" else "hold",
            "replacement_decision": row.get("replacement_decision"),
            "legacy_semantic_fingerprint": row.get("legacy_semantic_fingerprint"),
            "core_semantic_fingerprint": row.get("core_semantic_fingerprint"),
            "similarity": row.get("similarity"),
            "behavioral_differences": row.get("behavioral_differences"),
        })
    legacy_root = project.root / LEGACY_ROOT
    if legacy_root.is_dir():
        for path in sorted(legacy_root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(project.root).as_posix()
            if any(e.get("legacy_path") == relative for e in entries):
                continue
            classification, replacement, action = _classify_dot_opsx(relative)
            entries.append({
                "legacy_path": relative,
                "classification": classification,
                "replacement": replacement,
                "preservation": "git_history" if action == "block_until_mapped" else "archive_or_copy",
                "action": action,
                "verification": "manifest_review",
                "disposition": "retire_after_verified" if action != "block_until_mapped" else "hold",
            })
    blocking = [e for e in entries if e.get("action") in {"block_until_mapped", "block_until_proven"}]
    removable = [e for e in entries if e.get("action") == "replace_with_core_managed"]
    plan = {
        "schema_version": MANIFEST_SCHEMA,
        "generated_at": _utc(),
        "legacy_detected": legacy_present(project),
        "command_matrix_status": matrix.get("status"),
        "command_matrix": matrix,
        "status": "BLOCKED" if blocking or matrix.get("status") == "BLOCKED" else "READY",
        "entry_count": len(entries),
        "blocking": blocking,
        "collisions_pending_replace": len(removable),
        "entries": entries,
        "plan_fingerprint": "",
    }
    plan["plan_fingerprint"] = _plan_fingerprint(plan)
    return plan


def _backup_root(project: Project) -> Path:
    return project.runs / "migration-backup" / _utc()


def _verify_backup_integrity(backup: Path, source: Path) -> None:
    from .context import OpsxError
    if not backup.is_file() or not source.is_file():
        raise OpsxError("MIGRATION_BACKUP_INCOMPLETE", f"Missing backup for {source}")
    if _digest(backup.read_bytes()) != _digest(source.read_bytes()):
        raise OpsxError("MIGRATION_BACKUP_CORRUPT", f"Backup digest mismatch for {source}")


async def apply_plan(project: Project, plan: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    from .context import OpsxError
    pending = await read_json(project.runs / "migration-pending" / "plan.json", {})
    fingerprint = plan.get("plan_fingerprint") or _plan_fingerprint(plan)
    if pending and pending.get("plan_fingerprint") != fingerprint:
        raise OpsxError("MIGRATION_PLAN_STALE", "Migration plan changed since last dry-run; regenerate plan.")
    if plan.get("status") == "BLOCKED":
        paths = ", ".join(e["legacy_path"] for e in plan.get("blocking", [])[:20])
        raise OpsxError("MIGRATION_BLOCKED", f"Unresolved legacy paths: {paths}")
    if not plan.get("legacy_detected") and not plan.get("collisions_pending_replace"):
        return {"status": "NO_LEGACY", "dry_run": dry_run}
    if dry_run:
        await write_json(project.runs / "migration-pending" / "plan.json", {
            "plan_fingerprint": fingerprint,
            "generated_at": plan.get("generated_at"),
            "status": plan.get("status"),
        })
        return {"status": "PLAN_ONLY", "dry_run": True, "plan_fingerprint": fingerprint, **plan}
    backup = _backup_root(project)
    backup.mkdir(parents=True, exist_ok=True)
    inventory: list[dict[str, Any]] = []
    for entry in plan.get("entries", []):
        source = project.root / entry["legacy_path"]
        if not source.is_file():
            continue
        action = entry["action"]
        dest = backup / entry["legacy_path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dest)
        _verify_backup_integrity(dest, source)
        record = {**entry, "backup": str(dest.relative_to(project.root).as_posix()),
                  "source_digest": _digest(source.read_bytes())}
        if action == "preserve_copy" and entry.get("replacement"):
            target = project.root / entry["replacement"]
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(dest, target)
            record["copied_to"] = entry["replacement"]
        elif action == "archive_only" and entry.get("replacement"):
            target = project.root / entry["replacement"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps({
                "legacy_path": entry["legacy_path"],
                "backup": record["backup"],
                "source_digest": record["source_digest"],
                "archived_at": _utc(),
            }, indent=2), encoding="utf-8")
            record["archived_index"] = entry["replacement"]
        inventory.append(record)
    recovery = {
        "schema_version": 1,
        "migration_id": backup.name,
        "backup_root": backup.relative_to(project.root).as_posix(),
        "plan_fingerprint": fingerprint,
        "inventory_count": len(inventory),
        "recovery": "Restore from backup_root preserving paths; committed legacy-evidence indexes durable meta.",
        "inventory": inventory,
    }
    await write_json(project.root / RECOVERY_RELATIVE, recovery)
    receipt = {"status": "APPLIED", "recovery": recovery, "plan_status": plan.get("status")}
    await write_json(project.runs / "receipts" / "migration" / f"{backup.name}.json", receipt)
    await write_json(project.runs / "migration-pending" / "plan.json", {
        "plan_fingerprint": fingerprint,
        "applied_at": _utc(),
        "backup_root": recovery["backup_root"],
    })
    return receipt


async def retire_legacy_tree(project: Project, *, installation_managed: dict[str, str]) -> dict[str, Any]:
    from .context import OpsxError
    metadata = await read_json(project.installation, {})
    if not metadata or not metadata.get("managed_files"):
        raise OpsxError("INSTALLATION_NOT_CONFIRMED", "Cannot retire legacy before confirmed OPSX install.")
    recovery = await read_json(project.root / RECOVERY_RELATIVE, {})
    if not recovery and legacy_present(project):
        raise OpsxError("MIGRATION_RECOVERY_MISSING", "Migration recovery contract missing; refuse legacy retirement.")
    removed: list[str] = []
    legacy = project.root / LEGACY_ROOT
    if legacy.is_dir():
        backup = project.runs / "migration-backup"
        latest = sorted([p for p in backup.iterdir() if p.is_dir()], reverse=True)[0] if backup.is_dir() else None
        for path in sorted(legacy.rglob("*"), reverse=True):
            if path.is_file():
                rel = path.relative_to(project.root).as_posix()
                if latest:
                    staged = latest / rel
                    if staged.is_file() and _digest(staged.read_bytes()) == _digest(path.read_bytes()):
                        path.unlink()
                        removed.append(rel)
                evidence = project.root / LEGACY_EVIDENCE / "dot-opsx-tree" / rel.replace(".opsx/", "")
                if path.is_file():
                    evidence.parent.mkdir(parents=True, exist_ok=True)
                    if not evidence.is_file():
                        shutil.copy2(path, evidence)
            elif path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass
        try:
            legacy.rmdir()
        except OSError:
            pass
    return {"status": "RETIRED", "removed": removed, "install_core_sha": metadata.get("core_sha")}


async def stage_proven_command_replacements(project: Project, plan: dict[str, Any]) -> dict[str, Any]:
    """Remove only matrix-proven command files after backup integrity is recorded."""
    from .context import OpsxError
    recovery = await read_json(project.root / RECOVERY_RELATIVE, {})
    if not recovery:
        raise OpsxError("MIGRATION_RECOVERY_MISSING", "Cannot stage command removal without recovery contract.")
    backup_root = project.root / recovery["backup_root"]
    removed = []
    for entry in plan.get("entries", []):
        if entry.get("action") != "replace_with_core_managed":
            continue
        source = project.root / entry["legacy_path"]
        if not source.is_file():
            continue
        backup = backup_root / entry["legacy_path"]
        _verify_backup_integrity(backup, source)
        source.unlink()
        removed.append(entry["legacy_path"])
    return {"status": "STAGED", "removed": removed}


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