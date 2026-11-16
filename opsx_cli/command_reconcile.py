"""Command reconciliation: prove semantics before replacing legacy managed files."""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .authority import neutralize_command_authority, semantic_fingerprint
from .installer import _digest


@dataclass(frozen=True)
class ReconcileResult:
    command: str
    legacy_digest: str
    core_digest: str
    legacy_fingerprint: str
    core_fingerprint: str
    similarity: float
    decision: str
    action: str
    preservation: str
    behavioral_note: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "legacy_digest": self.legacy_digest,
            "core_digest": self.core_digest,
            "legacy_semantic_fingerprint": self.legacy_fingerprint,
            "core_semantic_fingerprint": self.core_fingerprint,
            "similarity": round(self.similarity, 6),
            "replacement_decision": self.decision,
            "migration_action": self.action,
            "preservation": self.preservation,
            "behavioral_differences": self.behavioral_note,
            "verification": "semantic_fingerprint" if self.decision == "PROVEN_EQUIVALENT" else "manual_review",
        }


def _normalized_body(text: str) -> str:
    return neutralize_command_authority(text)


def _similarity(legacy: str, core: str) -> float:
    return difflib.SequenceMatcher(None, _normalized_body(legacy), _normalized_body(core)).ratio()


def reconcile_command(name: str, legacy_text: str, core_text: str) -> ReconcileResult:
    legacy_fp = semantic_fingerprint(legacy_text)
    core_fp = semantic_fingerprint(core_text)
    ratio = _similarity(legacy_text, core_text)
    if legacy_fp == core_fp:
        decision, action = "PROVEN_EQUIVALENT", "replace_with_core_managed"
        note = "Neutralized semantic fingerprints match."
    elif ratio >= 0.995:
        decision, action = "PROVEN_NEAR_EQUIVALENT", "replace_with_core_managed"
        note = f"Similarity {ratio:.4f}; treat as proven after neutralization."
    elif ratio >= 0.92 and legacy_fp != core_fp:
        decision, action = "BEHAVIORAL_DRIFT", "block_until_proven"
        note = f"Similarity {ratio:.4f} but fingerprint differs; manual reconciliation required."
    else:
        decision, action = "UNRESOLVED", "block_until_proven"
        note = f"Similarity {ratio:.4f}; legacy and core semantics diverge."

    return ReconcileResult(
        command=name,
        legacy_digest=_digest(legacy_text.encode("utf-8")),
        core_digest=_digest(core_text.encode("utf-8")),
        legacy_fingerprint=legacy_fp,
        core_fingerprint=core_fp,
        similarity=ratio,
        decision=decision,
        action=action,
        preservation="managed_file_digest_map" if action == "replace_with_core_managed" else "legacy_until_proven",
        behavioral_note=note,
    )


def build_command_matrix(
    project: Path,
    *,
    core: Path,
    legacy_commands_root: Path | None = None,
    waivers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Reconcile overlapping opsx commands between legacy donor and core."""
    waivers = waivers or {}
    legacy_root = legacy_commands_root or (project / ".cursor" / "commands")
    core_commands = {p.name: p for p in (core / ".cursor/commands").glob("opsx*.md")}
    rows: list[dict[str, Any]] = []
    for path in sorted(legacy_root.glob("opsx*.md")):
        if not path.is_file():
            continue
        core_path = core_commands.get(path.name)
        if not core_path:
            rows.append({
                "command": path.name,
                "replacement_decision": "LEGACY_ONLY",
                "migration_action": "block_until_proven",
                "preservation": "project_owned",
            })
            continue
        legacy_text = path.read_text(encoding="utf-8")
        core_text = core_path.read_text(encoding="utf-8")
        row = reconcile_command(path.name, legacy_text, core_text).as_dict()
        waiver = waivers.get(path.name)
        if waiver == "CORE_CANONICAL" and row["migration_action"] == "block_until_proven":
            row["replacement_decision"] = "CORE_CANONICAL_WAIVER"
            row["migration_action"] = "replace_with_core_managed"
            row["preservation"] = "owner_waiver"
            row["behavioral_differences"] = f"Owner waiver CORE_CANONICAL: {waiver}"
        rows.append(row)
    blocked = [r for r in rows if r.get("migration_action") == "block_until_proven"]
    proven = [r for r in rows if r.get("migration_action") == "replace_with_core_managed"]
    return {
        "schema_version": 1,
        "command_count": len(rows),
        "proven_or_waived": len(proven),
        "blocked": len(blocked),
        "status": "BLOCKED" if blocked else "READY",
        "commands": rows,
    }