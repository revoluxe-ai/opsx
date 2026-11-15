"""Инвентаризация и idempotent-миграция исторических run_dialogs в opsx/tests/recordings.

Исходные ``run_dialogs`` только копируются; байты источника не изменяются.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

class MigrationBlocked(ValueError):
    """Повторная миграция обнаружила tamper/conflict — fail closed."""


Provenance = Literal[
    "REAL_COMPLETE",
    "REAL_PARTIAL",
    "SYNTHETIC_TEST",
    "AMBIGUOUS",
    "CORRUPTED",
]

_SCHEMA = "opsx.historical_dialog.v1"
_SENSITIVE = (
    re.compile(rb"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    re.compile(rb"\b(?:ghp_|gho_|ghu_|ghs_|github_pat_)[A-Za-z0-9_]{12,}"),
    re.compile(rb"\bsk-[A-Za-z0-9_-]{16,}"),
)
_SDK_SIDEcars = (
    "assistant.txt",
    "stream.ndjson",
    "report.json",
)


@dataclass(frozen=True)
class InventoryEntry:
    source_dir: str
    agent: str
    change_id: str
    run_uuid: str
    classification: Provenance
    files: tuple[str, ...]
    sha256: dict[str, str]
    sdk_run_id: str | None
    model_id: str | None
    replay_ready: bool
    missing_for_sdk_contract: tuple[str, ...]
    migration_target: str | None
    blocked_reason: str | None


def _digest_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _digest(path: Path) -> str:
    return _digest_bytes(path.read_bytes())


def _scan_secrets(path: Path) -> str | None:
    blob = path.read_bytes()
    for pattern in _SENSITIVE:
        if pattern.search(blob) is not None:
            return path.name
    return None


def _read_meta(agent_dir: Path) -> dict[str, Any] | None:
    meta_path = agent_dir / "meta.json"
    if not meta_path.is_file():
        return None
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _classify_agent_dir(agent_dir: Path, repo_root: Path) -> InventoryEntry:
    try:
        rel_path = agent_dir.relative_to(repo_root.resolve())
    except ValueError:
        rel_path = agent_dir
    rel = rel_path.as_posix()
    parts = rel_path.parts
    agent = agent_dir.name
    run_uuid = agent_dir.parent.name if len(agent_dir.parts) >= 2 else "unknown"
    change_id = agent_dir.parent.parent.name if len(agent_dir.parts) >= 3 else "unknown"

    files = sorted(p.name for p in agent_dir.iterdir() if p.is_file())
    sha256 = {name: _digest(agent_dir / name) for name in files}

    meta = _read_meta(agent_dir)
    if meta and isinstance(meta.get("agent"), str) and meta["agent"].strip():
        agent = meta["agent"].strip()
    sdk_run_id = str(meta.get("sdk_run_id") or "").strip() or None if meta else None
    model_id = str(meta.get("model_id") or "").strip() or None if meta else None

    if not files:
        classification: Provenance = "CORRUPTED"
    elif "conversation.json" not in files:
        classification = "AMBIGUOUS"
    elif any(name.endswith(".py") for name in files):
        classification = "SYNTHETIC_TEST"
    else:
        classification = "REAL_PARTIAL"

    missing = tuple(name for name in _SDK_SIDEcars if name not in files)
    replay_ready = classification == "REAL_PARTIAL" and bool(files)

    return InventoryEntry(
        source_dir=rel,
        agent=agent,
        change_id=change_id,
        run_uuid=run_uuid,
        classification=classification,
        files=tuple(files),
        sha256=sha256,
        sdk_run_id=sdk_run_id,
        model_id=model_id,
        replay_ready=replay_ready,
        missing_for_sdk_contract=missing,
        migration_target=None,
        blocked_reason=None,
    )


def discover(repo_root: Path) -> list[InventoryEntry]:
    """Read-only инвентаризация ``run_dialogs`` и legacy agents_outputs."""
    entries: list[InventoryEntry] = []
    roots = [
        repo_root / "opsx/opsx_runtime/run_dialogs",
        repo_root / "opsx/opsx_runtime/agents/tests/agents_outputs",
    ]
    for root in roots:
        if not root.is_dir():
            continue
        for meta_path in sorted(root.glob("*/*/*/meta.json")):
            entries.append(_classify_agent_dir(meta_path.parent, repo_root))
    # dedupe by source_dir
    seen: set[str] = set()
    unique: list[InventoryEntry] = []
    for entry in entries:
        if entry.source_dir in seen:
            continue
        seen.add(entry.source_dir)
        unique.append(entry)
    return unique


def _migration_target(repo_root: Path, entry: InventoryEntry) -> Path:
    key = f"{entry.change_id}-{entry.run_uuid}"
    return repo_root / "opsx/tests/recordings" / entry.agent / key


def _verify_existing_migration(
    target: Path,
    entry: InventoryEntry,
    source: Path,
) -> None:
    manifest_path = target / "manifest.json"
    if not manifest_path.is_file():
        raise MigrationBlocked("MIGRATION_MANIFEST_MISSING")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    identity = manifest.get("identity")
    if not isinstance(identity, dict):
        raise MigrationBlocked("MIGRATION_IDENTITY_MISSING")
    for key, expected in (
        ("change_id", entry.change_id),
        ("run_uuid", entry.run_uuid),
        ("agent", entry.agent),
    ):
        if identity.get(key) != expected:
            raise MigrationBlocked(f"MIGRATION_IDENTITY_MISMATCH:{key}")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise MigrationBlocked("MIGRATION_ARTIFACTS_MISSING")
    for name in entry.files:
        src = source / name
        dst = target / name
        if not dst.is_file():
            raise MigrationBlocked(f"MIGRATION_COPY_MISSING:{name}")
        src_sha = _digest(src)
        dst_sha = _digest(dst)
        meta = artifacts.get(name)
        if not isinstance(meta, dict):
            raise MigrationBlocked(f"MIGRATION_ARTIFACT_META_MISSING:{name}")
        if meta.get("source_sha256") != entry.sha256.get(name):
            raise MigrationBlocked(f"MIGRATION_SOURCE_DIGEST_STALE:{name}")
        if meta.get("copied_sha256") != dst_sha or dst_sha != src_sha:
            raise MigrationBlocked(f"MIGRATION_DIGEST_MISMATCH:{name}")


def migrate(repo_root: Path, entries: list[InventoryEntry], *, dry_run: bool = False) -> list[InventoryEntry]:
    """Копировать REAL_PARTIAL записи в Git-tracked recordings (manifest only promotion)."""
    updated: list[InventoryEntry] = []
    for entry in entries:
        target = _migration_target(repo_root, entry)
        entry = InventoryEntry(**{**asdict(entry), "migration_target": str(target.relative_to(repo_root))})
        if entry.classification != "REAL_PARTIAL":
            updated.append(entry)
            continue
        source = repo_root / entry.source_dir
        for name in entry.files:
            secret = _scan_secrets(source / name)
            if secret is not None:
                entry = InventoryEntry(
                    **{
                        **asdict(entry),
                        "blocked_reason": f"OWNER_ATTENTION:POSSIBLE_SECRET:{secret}",
                    }
                )
                updated.append(entry)
                break
        else:
            lock_key = _digest_bytes(
                f"{entry.change_id}\0{entry.run_uuid}\0{entry.agent}".encode("utf-8")
            )[:24]
            lock_dir = target.parent / f".migrate-lock-{lock_key}"
            staging_dir = target.parent / f".migrate-staging-{lock_key}"
            if staging_dir.exists() and not (target / "manifest.json").is_file():
                entry = InventoryEntry(
                    **{**asdict(entry), "blocked_reason": "MIGRATION_RECOVERY_REQUIRED"}
                )
                updated.append(entry)
                continue
            if (target / "manifest.json").is_file():
                try:
                    _verify_existing_migration(target, entry, source)
                except MigrationBlocked as exc:
                    entry = InventoryEntry(**{**asdict(entry), "blocked_reason": str(exc)})
                updated.append(entry)
                continue
            if dry_run:
                updated.append(entry)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                lock_dir.mkdir(exist_ok=False)
            except FileExistsError:
                entry = InventoryEntry(
                    **{**asdict(entry), "blocked_reason": "MIGRATION_LOCK_HELD"}
                )
                updated.append(entry)
                continue
            try:
                if staging_dir.exists():
                    raise MigrationBlocked("MIGRATION_STAGING_LEFTOVER")
                staging_dir.mkdir(mode=0o700, parents=False)
                copied: dict[str, dict[str, Any]] = {}
                for name in entry.files:
                    src = source / name
                    dst = staging_dir / name
                    shutil.copy2(src, dst)
                    copied[name] = {
                        "source_sha256": entry.sha256[name],
                        "copied_sha256": _digest(dst),
                        "bytes": dst.stat().st_size,
                    }
                manifest = {
                    "schema": _SCHEMA,
                    "provenance_class": entry.classification,
                    "recording_complete_for_sdk_replay": False,
                    "owner_review_attested": False,
                    "missing_for_sdk_contract": list(entry.missing_for_sdk_contract),
                    "identity": {
                        "change_id": entry.change_id,
                        "run_uuid": entry.run_uuid,
                        "agent": entry.agent,
                        "sdk_run_id": entry.sdk_run_id,
                        "model_id": entry.model_id,
                    },
                    "source_paths": [entry.source_dir],
                    "artifacts": copied,
                    "verification_status": "HISTORICAL_PARTIAL",
                }
                (staging_dir / "manifest.json").write_text(
                    json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                verify_historical_fixture(staging_dir)
                staging_dir.rename(target)
            except (MigrationBlocked, OSError, ValueError) as exc:
                if staging_dir.exists():
                    shutil.rmtree(staging_dir, ignore_errors=True)
                entry = InventoryEntry(**{**asdict(entry), "blocked_reason": str(exc)})
            finally:
                if lock_dir.exists():
                    lock_dir.rmdir()
            updated.append(entry)
    return updated


def write_inventory(repo_root: Path, entries: list[InventoryEntry]) -> Path:
    path = repo_root / "opsx/tests/MIGRATION_INVENTORY.json"
    payload = {
        "schema": "opsx.migration_inventory.v1",
        "entries": [asdict(entry) for entry in entries],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def verify_historical_fixture(path: Path) -> dict[str, Any]:
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != _SCHEMA:
        raise ValueError("HISTORICAL_MANIFEST_SCHEMA_MISMATCH")
    if manifest.get("provenance_class") != "REAL_PARTIAL":
        raise ValueError("HISTORICAL_NOT_PARTIAL")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("HISTORICAL_ARTIFACTS_MISSING")
    for name, meta in artifacts.items():
        blob = (path / name).read_bytes()
        if _digest_bytes(blob) != meta.get("copied_sha256"):
            raise ValueError(f"HISTORICAL_DIGEST_MISMATCH:{name}")
    return manifest


def main() -> int:
    cli = argparse.ArgumentParser(description="OPSX historical dialog discovery and migration")
    cli.add_argument("--repo-root", type=Path, default=Path.cwd())
    cli.add_argument("--discover-only", action="store_true")
    cli.add_argument("--migrate", action="store_true")
    cli.add_argument("--dry-run", action="store_true")
    args = cli.parse_args()
    repo_root = args.repo_root.resolve()
    entries = discover(repo_root)
    if args.migrate or args.dry_run:
        entries = migrate(repo_root, entries, dry_run=args.dry_run)
    inventory = write_inventory(repo_root, entries)
    counts: dict[str, int] = {}
    for entry in entries:
        counts[entry.classification] = counts.get(entry.classification, 0) + 1
    print(json.dumps({"inventory": str(inventory), "counts": counts, "total": len(entries)}, ensure_ascii=False))
    if args.discover_only and not args.migrate:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
