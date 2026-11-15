"""Deterministic export: ``opsx/project/capabilities.yaml`` → ``capabilities.json``.

YAML is the authoritative configuration source. JSON consumed by Temporal and the
activation registry MUST match the export of YAML at HEAD. Drift is rejected;
there is no silent fallback to stale JSON.
"""

from __future__ import annotations

from opsx.project_context import resource_path

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

EXPORTER_IMPLEMENTATION_ID = "capabilities-export-v1.0.0"

DEFAULT_YAML = Path("opsx/project/capabilities.yaml")
DEFAULT_JSON = Path("opsx/project/capabilities.json")

_QUALITY_SLOTS: tuple[tuple[str, str], ...] = (
    ("install", "install_command"),
    ("syntax", "syntax_command"),
    ("test", "test_command"),
    ("migration_apply", "migration_apply_command"),
    ("migration_drift", "migration_drift_command"),
    ("typecheck", "typecheck_command"),
    ("lint", "lint_command"),
    ("build", "build_command"),
)


class CapabilitiesExportError(Exception):
    """Export or drift validation failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def repo_root_from_module() -> Path:
    return Path(__file__).resolve().parents[2]


def yaml_sha256(path: Path) -> str:
    raw = path.read_bytes()
    return hashlib.sha256(raw).hexdigest()


def load_capabilities_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise CapabilitiesExportError("MISSING_YAML", f"missing: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise CapabilitiesExportError("INVALID_YAML", "root must be a mapping")
    return data


def _quality_from_yaml(quality: Any) -> dict[str, Any]:
    if not isinstance(quality, dict):
        raise CapabilitiesExportError("INVALID_YAML", "quality must be a mapping")
    out: dict[str, Any] = {}
    for yaml_key, json_key in _QUALITY_SLOTS:
        slot = quality.get(yaml_key)
        if slot is None:
            out[json_key] = None
            continue
        if not isinstance(slot, dict):
            raise CapabilitiesExportError(
                "INVALID_YAML",
                f"quality.{yaml_key} must be a mapping",
            )
        cmd = slot.get("command")
        out[json_key] = cmd if cmd is None else str(cmd)
    return out


def _durability_from_yaml(durability: Any) -> dict[str, Any]:
    if not isinstance(durability, dict):
        raise CapabilitiesExportError("INVALID_YAML", "durability must be a mapping")
    out = dict(durability)
    if out.get("amocrm_reconciliation") == "unresolved":
        out["amocrm_reconciliation"] = None
    return out


def _control_plane_from_yaml(control_plane: Any) -> dict[str, Any]:
    if not isinstance(control_plane, dict):
        raise CapabilitiesExportError("INVALID_YAML", "control_plane must be a mapping")
    out: dict[str, Any] = {}
    for key, value in control_plane.items():
        if not isinstance(value, dict):
            out[key] = value
            continue
        cleaned = {k: v for k, v in value.items() if k != "note"}
        out[key] = cleaned
    temporal = out.get("temporal")
    if isinstance(temporal, dict) and "tested_revision" not in temporal:
        temporal["tested_revision"] = "a01de91ba9685bd81e6615c04c093b60e1c5a13b"
    return out


def export_document_from_yaml(data: dict[str, Any]) -> dict[str, Any]:
    """Build the machine JSON document from a parsed YAML mapping (no metadata)."""
    required_top = (
        "schema_version",
        "target",
        "adapter",
        "status",
        "facts_revision",
        "product",
        "quality",
        "durability",
        "runtime_services",
        "unresolved_product_capabilities",
        "resolution_policy",
    )
    for key in required_top:
        if key not in data:
            raise CapabilitiesExportError("INVALID_YAML", f"missing top-level key: {key}")

    doc: dict[str, Any] = {
        "schema_version": data["schema_version"],
        "target": data["target"],
        "adapter": data["adapter"],
        "status": data["status"],
        "facts_revision": data["facts_revision"],
        "product": dict(data["product"]),
        "quality": _quality_from_yaml(data["quality"]),
        "durability": _durability_from_yaml(data["durability"]),
        "runtime_services": dict(data["runtime_services"]),
        "unresolved_product_capabilities": list(data["unresolved_product_capabilities"]),
        "resolution_policy": dict(data["resolution_policy"]),
    }
    if "interfaces" in data:
        doc["interfaces"] = dict(data["interfaces"])
    if "channel_impact" in data:
        doc["channel_impact"] = dict(data["channel_impact"])
    if "control_plane" in data:
        doc["control_plane"] = _control_plane_from_yaml(data["control_plane"])
    return doc


def canonical_json_bytes(document: dict[str, Any]) -> bytes:
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False).encode(
        "utf-8"
    ) + b"\n"


def export_with_metadata(
    yaml_path: Path,
    *,
    exported_at: datetime | None = None,
) -> dict[str, Any]:
    """Export YAML to JSON document including ``export_metadata``."""
    data = load_capabilities_yaml(yaml_path)
    body = export_document_from_yaml(data)
    when = exported_at or datetime.now(timezone.utc)
    body["export_metadata"] = {
        "exporter_implementation_id": EXPORTER_IMPLEMENTATION_ID,
        "source_yaml_path": str(yaml_path.as_posix()),
        "source_yaml_sha256": yaml_sha256(yaml_path),
        "exported_at_utc": when.replace(microsecond=0).isoformat(),
    }
    return body


def read_json_document(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise CapabilitiesExportError("MISSING_JSON", f"missing: {path}")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def assert_json_matches_yaml(
    yaml_path: Path,
    json_path: Path,
    *,
    require_fresh_metadata: bool = True,
) -> None:
    """Fail closed if on-disk JSON ≠ deterministic export of YAML."""
    expected = export_document_from_yaml(load_capabilities_yaml(yaml_path))
    actual = read_json_document(json_path)
    metadata = actual.pop("export_metadata", None)
    if require_fresh_metadata:
        if not isinstance(metadata, dict):
            raise CapabilitiesExportError(
                "DRIFT_METADATA_MISSING",
                "capabilities.json missing export_metadata",
            )
        if metadata.get("source_yaml_sha256") != yaml_sha256(yaml_path):
            raise CapabilitiesExportError(
                "DRIFT_STALE_METADATA",
                "export_metadata.source_yaml_sha256 does not match current YAML",
            )
        if metadata.get("exporter_implementation_id") != EXPORTER_IMPLEMENTATION_ID:
            raise CapabilitiesExportError(
                "DRIFT_EXPORTER_VERSION",
                "capabilities.json exported with unknown exporter version",
            )
    if actual != expected:
        raise CapabilitiesExportError(
            "DRIFT_BODY_MISMATCH",
            "capabilities.json body does not match YAML export",
        )


def write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(document))


def cmd_export(yaml_path: Path, json_path: Path) -> int:
    document = export_with_metadata(yaml_path)
    write_json(json_path, document)
    print(f"EXPORT_OK: {json_path}")
    return 0


def cmd_verify(yaml_path: Path, json_path: Path) -> int:
    try:
        assert_json_matches_yaml(yaml_path, json_path, require_fresh_metadata=True)
    except CapabilitiesExportError as error:
        print(f"{error.code}: {error.message}", file=sys.stderr)
        return 1
    print("VERIFY_OK: capabilities.json matches YAML export")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OPSX capabilities YAML→JSON export")
    parser.add_argument(
        "command",
        choices=("export", "verify"),
        help="export: write JSON from YAML; verify: reject drift",
    )
    parser.add_argument("--yaml", type=Path, default=DEFAULT_YAML)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    args = parser.parse_args(argv)
    root = repo_root_from_module()
    yaml_path = args.yaml if args.yaml.is_absolute() else resource_path(root, args.yaml)
    json_path = args.json if args.json.is_absolute() else resource_path(root, args.json)
    if args.command == "export":
        return cmd_export(yaml_path, json_path)
    return cmd_verify(yaml_path, json_path)


if __name__ == "__main__":
    raise SystemExit(main())
