#!/usr/bin/env python3
"""Change-local documentation capture (agent layer → deterministic gate).

Агент (Context7/MCP/read-only fetch) записывает bytes и manifest;
gate проверяет пути, digest, identity и claim binding без live network.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CAPTURE_SCHEMA_VERSION = 1
CAPTURE_DIR = Path("architecture-evidence/captured-docs")
ALLOWED_ACQUISITION_TOOLS = frozenset(
    {
        "context7-mcp",
        "official-provider-export",
        "agent-readonly-fetch",
        "manual_unattested",
    }
)
TRUSTED_ACQUISITION_TOOLS = frozenset(
    {
        "context7-mcp",
        "official-provider-export",
        "agent-readonly-fetch",
    }
)
CAPTURE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


@dataclass
class CaptureFinding:
    code: str
    detail: str
    claim_id: str | None = None


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _safe_capture_paths(change_root: Path, capture_id: str) -> tuple[Path, Path]:
    if not CAPTURE_ID_PATTERN.match(capture_id):
        raise ValueError("invalid capture_id")
    base = (change_root / CAPTURE_DIR).resolve()
    content = (base / f"{capture_id}.md").resolve()
    meta = (base / f"{capture_id}.meta.json").resolve()
    if base not in content.parents or base not in meta.parents:
        raise ValueError("path traversal")
    return content, meta


def record_capture(
    *,
    change_root: Path,
    capture_id: str,
    change_id: str,
    inputs_digest: str,
    source_uri: str,
    publisher: str,
    documented_version: str,
    acquisition_tool: str,
    acquisition_occurred: bool,
    claim_ids: list[str],
    content: bytes,
    captured_at: str | None = None,
) -> dict[str, Any]:
    """Атомарно записать capture (content + meta). Idempotent при идентичных bytes."""

    if acquisition_tool not in ALLOWED_ACQUISITION_TOOLS:
        raise ValueError("acquisition_tool not allowed")
    if acquisition_tool in TRUSTED_ACQUISITION_TOOLS and not acquisition_occurred:
        raise ValueError("acquisition_occurred required for attested tools")

    content_path, meta_path = _safe_capture_paths(change_root, capture_id)
    content_path.parent.mkdir(parents=True, exist_ok=True)
    digest = sha256_bytes(content)
    rel_content = content_path.relative_to(change_root).as_posix()

    if meta_path.is_file():
        existing = json.loads(meta_path.read_text(encoding="utf-8"))
        if existing.get("content_sha256") != digest:
            raise ValueError("capture conflict: digest mismatch for capture_id")
        if existing.get("change_id") != change_id or existing.get("inputs_digest") != inputs_digest:
            raise ValueError("capture conflict: identity mismatch")
        return existing

    tmp_content = content_path.with_suffix(".md.tmp")
    tmp_meta = meta_path.with_suffix(".json.tmp")
    tmp_content.write_bytes(content)
    manifest: dict[str, Any] = {
        "capture_schema_version": CAPTURE_SCHEMA_VERSION,
        "capture_id": capture_id,
        "change_id": change_id,
        "inputs_digest": inputs_digest,
        "source_uri": source_uri,
        "publisher": publisher,
        "documented_version": documented_version,
        "acquisition_tool": acquisition_tool,
        "acquisition_occurred": acquisition_occurred,
        "captured_at": captured_at or datetime.now(timezone.utc).isoformat(),
        "content_sha256": digest,
        "content_path": rel_content,
        "claim_ids": claim_ids,
        "integrity_status": "verified",
        "provenance_trust": (
            "agent_attested_s2"
            if acquisition_tool in TRUSTED_ACQUISITION_TOOLS and acquisition_occurred
            else "unverified_s2"
        ),
    }
    tmp_meta.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_content.replace(content_path)
    tmp_meta.replace(meta_path)
    return manifest


def load_capture_manifest(change_root: Path, capture_id: str) -> dict[str, Any] | None:
    try:
        _, meta_path = _safe_capture_paths(change_root, capture_id)
    except ValueError:
        return None
    if not meta_path.is_file():
        return None
    return json.loads(meta_path.read_text(encoding="utf-8"))


def validate_captures_for_receipt(
    receipt: dict[str, Any],
    *,
    change_root: Path,
    expected_change_id: str,
    expected_inputs_digest: str,
) -> list[CaptureFinding]:
    """Проверить captured_doc sources и manifests (offline)."""

    findings: list[CaptureFinding] = []
    receipt_inputs_digest = str(receipt.get("inputs_digest") or "")

    for claim in receipt.get("claims") or []:
        if not isinstance(claim, dict) or claim.get("result") != "PASS":
            continue
        cid = str(claim.get("claim_id"))
        for source in claim.get("sources") or []:
            if not isinstance(source, dict) or source.get("kind") != "captured_doc":
                continue
            capture_id = str(source.get("capture_id") or "")
            manifest = load_capture_manifest(change_root, capture_id)
            if manifest is None:
                findings.append(
                    CaptureFinding(
                        "DOC_CAPTURE_MISSING",
                        f"missing capture manifest {capture_id}",
                        cid,
                    )
                )
                continue

            if manifest.get("change_id") != expected_change_id:
                findings.append(
                    CaptureFinding(
                        "DOC_CAPTURE_IDENTITY_MISMATCH",
                        "capture change_id mismatch",
                        cid,
                    )
                )
            if manifest.get("inputs_digest") != expected_inputs_digest:
                findings.append(
                    CaptureFinding(
                        "STALE_ARCHITECTURE_EVIDENCE",
                        "capture inputs_digest stale vs change",
                        cid,
                    )
                )
            if manifest.get("inputs_digest") != receipt_inputs_digest:
                findings.append(
                    CaptureFinding(
                        "STALE_ARCHITECTURE_EVIDENCE",
                        "capture inputs_digest stale vs receipt",
                        cid,
                    )
                )

            rel_path = manifest.get("content_path") or ""
            try:
                content_path = (change_root / rel_path).resolve()
                base = (change_root / CAPTURE_DIR).resolve()
                if base not in content_path.parents:
                    findings.append(
                        CaptureFinding("DOC_CAPTURE_PATH_UNSAFE", rel_path, cid)
                    )
                    continue
            except (OSError, ValueError):
                findings.append(CaptureFinding("DOC_CAPTURE_PATH_UNSAFE", rel_path, cid))
                continue

            if not content_path.is_file():
                findings.append(
                    CaptureFinding("DOC_CAPTURE_MISSING", f"missing {rel_path}", cid)
                )
                continue

            actual = sha256_file(content_path)
            declared = manifest.get("content_sha256")
            if actual != declared or actual != source.get("content_sha256"):
                findings.append(
                    CaptureFinding(
                        "SOURCE_DIGEST_MISMATCH",
                        f"capture digest mismatch {capture_id}",
                        cid,
                    )
                )

            if cid not in (manifest.get("claim_ids") or []):
                findings.append(
                    CaptureFinding(
                        "DOC_CAPTURE_CLAIM_MISMATCH",
                        f"claim {cid} not in capture claim_ids",
                        cid,
                    )
                )

            tool = manifest.get("acquisition_tool")
            if tool in TRUSTED_ACQUISITION_TOOLS and not manifest.get("acquisition_occurred"):
                findings.append(
                    CaptureFinding(
                        "DOC_ACQUISITION_NOT_ATTESTED",
                        "acquisition_tool set but acquisition_occurred false",
                        cid,
                    )
                )

            if manifest.get("provenance_trust") == "unverified_s2":
                findings.append(
                    CaptureFinding(
                        "PROVENANCE_NOT_TRUSTED_S2",
                        "integrity ok; trusted provenance requires agent-attested capture",
                        cid,
                    )
                )

            library = claim.get("library") or {}
            version_claim = library.get("version_claim")
            doc_version = manifest.get("documented_version")
            if version_claim and doc_version and version_claim != doc_version:
                findings.append(
                    CaptureFinding(
                        "LIBRARY_API_INCOMPATIBLE",
                        f"documented_version {doc_version} != claim {version_claim}",
                        cid,
                    )
                )

    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record architecture evidence doc capture")
    parser.add_argument("command", choices=["record"])
    parser.add_argument("--change-root", type=Path, required=True)
    parser.add_argument("--capture-id", required=True)
    parser.add_argument("--change-id", required=True)
    parser.add_argument("--inputs-digest", required=True)
    parser.add_argument("--source-uri", required=True)
    parser.add_argument("--publisher", required=True)
    parser.add_argument("--documented-version", required=True)
    parser.add_argument("--acquisition-tool", required=True)
    parser.add_argument("--acquisition-occurred", choices=("true", "false"), required=True)
    parser.add_argument("--claim-id", action="append", required=True)
    parser.add_argument("--content-file", type=Path, required=True)
    args = parser.parse_args(argv)

    content = args.content_file.read_bytes()
    try:
        manifest = record_capture(
            change_root=args.change_root.resolve(),
            capture_id=args.capture_id,
            change_id=args.change_id,
            inputs_digest=args.inputs_digest,
            source_uri=args.source_uri,
            publisher=args.publisher,
            documented_version=args.documented_version,
            acquisition_tool=args.acquisition_tool,
            acquisition_occurred=args.acquisition_occurred == "true",
            claim_ids=args.claim_id,
            content=content,
        )
    except ValueError as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "manifest": manifest}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
