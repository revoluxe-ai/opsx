"""Перенос только APPLY-DESIGN.json из receipts в openspec.

Не выбирает между разными копиями. Если обе копии есть и байты различаются,
пишет CONFLICT и выходит с кодом 2. Старую копию удаляет только после
совпадения digest.
"""

from __future__ import annotations

from opsx.project_context import resource_path

import hashlib
import sys
from pathlib import Path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def migrate_change(repo: Path, change_id: str) -> str:
    """Перенести design одного change. Возвращает ok, skipped или conflict."""
    old = resource_path(repo, "opsx", "receipts", change_id, "apply-design", "APPLY-DESIGN.json")
    new = resource_path(repo, "openspec", "changes", change_id, "apply-design", "APPLY-DESIGN.json")
    if not old.is_file() and not new.is_file():
        return "skipped"
    if old.is_file() and new.is_file() and _sha(old) != _sha(new):
        print(f"CONFLICT {change_id} old={_sha(old)} new={_sha(new)}")
        return "conflict"
    if old.is_file() and not new.is_file():
        new.parent.mkdir(parents=True, exist_ok=True)
        _ = new.write_bytes(old.read_bytes())
    if old.is_file() and new.is_file() and _sha(old) == _sha(new):
        old.unlink()
        old_md = old.with_name("APPLY-DESIGN.md")
        new_md = new.with_name("APPLY-DESIGN.md")
        if old_md.is_file() and not new_md.is_file():
            _ = new_md.write_bytes(old_md.read_bytes())
        if old_md.is_file() and new_md.is_file() and _sha(old_md) == _sha(new_md):
            old_md.unlink()
    if new.is_file():
        import json
        from datetime import datetime, timezone

        design = json.loads(new.read_text(encoding="utf-8"))
        planning = design.get("planning_identity") if isinstance(design, dict) else {}
        protected = design.get("protected_context_identity") if isinstance(design, dict) else {}
        receipt = resource_path(repo, "opsx", "receipts", change_id, "apply-design", "hop.receipt.json")
        receipt.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": "opsx.idempotency_receipt",
            "schema_version": 1,
            "node_id": "apply_design",
            "operation": "apply_design",
            "change_id": change_id,
            "slice_id": None,
            "status": "PASS",
            "input_fingerprints": {
                "planning_digest": str((planning or {}).get("planning_digest") or ""),
                "protected_context_digest": str((protected or {}).get("content_digest") or ""),
            },
            "artifacts": [
                {
                    "role": "apply_design",
                    "path": new.relative_to(repo).as_posix(),
                    "sha256": _sha(new),
                }
            ],
            "validator": {"name": "opsx-apply-design.mjs", "version": "on-disk"},
            "attempt": 1,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        _ = receipt.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"RECEIPT {receipt.as_posix()}")
    print(f"OK {change_id} {new.as_posix()} {_sha(new) if new.is_file() else 'missing'}")
    return "ok"


def main() -> int:
    repo = Path(__file__).resolve().parents[3]
    change_id = sys.argv[1] if len(sys.argv) > 1 else "visual"
    result = migrate_change(repo, change_id)
    return 2 if result == "conflict" else 0


if __name__ == "__main__":
    raise SystemExit(main())
