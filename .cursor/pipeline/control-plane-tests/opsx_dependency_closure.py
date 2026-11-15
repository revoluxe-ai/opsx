"""Dependency-closure verifier for activated OPSX commands (opsx-dependency-closure-v0).

Recomputes mandatory portable dependencies for each active command, validates
OpenSpec config via ``openspec/tools/check_config_contract.py``, and binds
results to HEAD identity fields. Read-only: no writes to product or policy trees.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

_repo_root = Path(__file__).resolve().parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from openspec.tools.check_config_contract import (
    CHECKER_IMPLEMENTATION_ID,
    ConfigContractError,
    load_and_validate_config,
)
from tools.openspec.openspec_cli_provider import (
    provider_identity,
    validate_openspec_cli_provider,
)

CLOSURE_PREDICATE_VERSION = "v0.1.4"
SCHEMA_VERSION = "opsx-dependency-closure-v0"
TARGET_REPO = "stroy-system"

GUARD_PATTERN = re.compile(
    r"poetry\s+run\s+python\s+tools/openspec/openspec_guard\.py",
    re.I,
)
CONFIG_CHECK_PATTERN = re.compile(
    r"openspec/tools/check_config_contract\.py",
    re.I,
)
OPEN_SPEC_GUARD_PATH_PATTERN = re.compile(
    r"tools/openspec/openspec_guard\.py",
    re.I,
)
DECISION_PATH_PATTERN = re.compile(
    r"openspec/decisions/[a-z0-9-]+/decision\.md",
    re.I,
)
APPLY_SCHEMA_PATTERN = re.compile(
    r"\opsx/registry/apply_[a-z0-9_]+\.schema\.json",
    re.I,
)

PRIMARY_OVERRIDE_BANNER = re.compile(
    r"^#\s*STROY-SYSTEM TARGET OVERRIDE\b",
    re.I,
)

DEFERRED_ACTIVATION_HASH_DRIFT_COMMANDS = frozenset({"opsx-spec-scenario-coverage"})

OPTIONAL_PATH_MARKERS = (
    "only if still needed",
    "when index exists",
    "when exists",
    "read_if_exists",
    "missing_is_not_blocker",
    "if still needed",
)

CAPABILITY_COMMAND_IDS: dict[str, frozenset[str]] = {
    "typecheck_command": frozenset(
        {
            "opsx-run-typecheck",
            "opsx-static-check",
            "opsx-fix-types",
            "opsx-static-check-coverage",
            "opsx-pyright-strict-coverage",
        }
    ),
    "build_gate": frozenset({"opsx-run-build"}),
    "lint_gate": frozenset({"opsx-lint", "opsx-run-lint"}),
}

LEGACY_EXCLUDED_COMMAND_IDS = frozenset({"opsx-bugbot-report"})

INSTALLED_ACTIVE_MANIFEST_STATES = frozenset(
    {
        "IMPLEMENTED_UNVERIFIED",
        "FUNCTIONAL_PROVEN",
        "ACTIVE_WITH_CAPABILITY_GATES",
        "ACTIVE_CORE",
    }
)

TRANSFER_MANIFEST_REL = "opsx/registry/command_transfer_manifest.md"

DONOR_PYTHON_ADAPTER_IDS = frozenset({"poetry-python", "python-poetry"})
TARGET_GENERIC_ADAPTER = "project-generic"
TARGET_QUALITY_GATES_PATH = "opsx/project/quality-gates.md"
TARGET_GENERIC_ADAPTER_README = "opsx/adapters/project-generic/README.md"

ARCHITECTURE_DECISION_GAPS = (
    {
        "area": "openspec/config.yaml context and rules.* authoring owner",
        "status": "Architecture Decision Missing",
        "evidence": "EVIDENCE_GAP",
    },
    {
        "area": "openspec/tools/check_config_contract.py enforcement owner",
        "status": "Architecture Decision Missing",
        "evidence": "EVIDENCE_GAP",
    },
    {
        "area": "tools/openspec/openspec_guard.py portable vs donor precondition",
        "status": "Architecture Decision Missing",
        "evidence": "EVIDENCE_GAP",
    },
    {
        "area": "who may set PROJECTIZATION_COMPLETE",
        "status": "Architecture Decision Missing",
        "evidence": "EVIDENCE_GAP",
    },
)


FailureClass = Literal[
    "MISSING_MANDATORY",
    "VALIDATOR_REJECTED",
    "MALFORMED_CONFIG",
    "UNSUPPORTED_KEY",
    "INVALID_RULES_SHAPE",
    "FOREIGN_DONOR_ASSUMPTION",
    "STALE_IDENTITY",
    "STALE_RECEIPT",
    "TARGET_CAPABILITY_UNRESOLVED",
    "FALSE_COMPLETE_CLAIM",
]


@dataclass
class FailureRecord:
    """Single closure failure or block entry."""

    command_id: str
    class_name: FailureClass
    path: str
    scope: Literal["GLOBAL", "COMMAND"]
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        payload: dict[str, str] = {
            "command_id": self.command_id,
            "class": self.class_name,
            "path": self.path,
            "scope": self.scope,
        }
        if self.detail:
            payload["detail"] = self.detail
        return payload


@dataclass
class DependencyInventoryRow:
    """Per-command dependency inventory row for machine-readable output."""

    command_id: str
    command_hash: str
    mandatory_paths: list[str] = field(default_factory=list)
    optional_paths: list[str] = field(default_factory=list)
    foreign_assumptions: list[str] = field(default_factory=list)
    capability_gates: list[str] = field(default_factory=list)
    status: str = "unknown"
    evidence_source: str = "command_activation.ndjson+command_bytes"

    def as_dict(self) -> dict[str, Any]:
        return {
            "command_id": self.command_id,
            "command_hash": self.command_hash,
            "mandatory_paths": self.mandatory_paths,
            "optional_paths": self.optional_paths,
            "foreign_assumptions": self.foreign_assumptions,
            "capability_gates": self.capability_gates,
            "status": self.status,
            "evidence_source": self.evidence_source,
        }


def git_head_revision(root: Path) -> str:
    """Return current HEAD commit SHA for ``root``."""

    return (
        subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"])
        .decode()
        .strip()
    )


def git_blob_sha256(root: Path, relative_path: str, revision: str = "HEAD") -> str | None:
    """Hash committed blob bytes at ``revision``, or None when path is absent."""

    try:
        blob = subprocess.check_output(
            ["git", "-C", str(root), "show", f"{revision}:{relative_path}"],
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return None
    return hashlib.sha256(blob).hexdigest()


def read_activation_rows(activation_path: Path) -> list[dict[str, Any]]:
    """Load command activation NDJSON."""

    rows: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(
        activation_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw_line.strip():
            continue
        value = json.loads(raw_line)
        if not isinstance(value, dict):
            raise ValueError(f"{activation_path}:{line_number}: row is not an object")
        rows.append(value)
    return rows


def _parse_frontmatter(body: str) -> dict[str, Any]:
    if not body.startswith("---\n"):
        return {}
    end = body.find("\n---\n", 4)
    if end < 0:
        return {}
    block = body[4:end]
    result: dict[str, Any] = {}
    for line in block.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1]
            items = [part.strip().strip('"') for part in inner.split(",") if part.strip()]
            result[key] = items
        else:
            result[key] = value.strip('"')
    return result


def _non_authority_spans(body: str) -> list[tuple[int, int]]:
    """Return line ranges for the primary donor disclaimer banner only.

    Only the initial ``# STROY-SYSTEM TARGET OVERRIDE`` block is non-authority.
    Later phrases such as ``Fail closed as in TARGET OVERRIDE`` or duplicate
    override headings must not suppress adapter reads or mandatory path cites.
    """

    spans: list[tuple[int, int]] = []
    lines = body.splitlines()
    start: int | None = None
    banner_end_markers = (
        "separate from product Temporal/runtime adoption.",
        "separate from product Temporal/runtime adoption",
    )
    for index, line in enumerate(lines):
        if start is None and PRIMARY_OVERRIDE_BANNER.match(line.strip()):
            start = index
            continue
        if start is None:
            continue
        if line.startswith("# /"):
            spans.append((start, index))
            break
        if any(marker in line for marker in banner_end_markers):
            spans.append((start, index + 1))
            break
    return spans


def _donor_adapter_read_superseded_by_target(
    path: str,
    capabilities: dict[str, Any],
) -> bool:
    """True when a donor adapter README cite maps to project-generic on TARGET."""

    if capabilities.get("adapter") != TARGET_GENERIC_ADAPTER:
        return False
    product = capabilities.get("product")
    if not isinstance(product, dict):
        return False
    if product.get("package_manager") != "pip":
        return False
    lowered = path.lower()
    return any(adapter_id in lowered for adapter_id in DONOR_PYTHON_ADAPTER_IDS)


def _remap_donor_adapter_mandatory_path(
    path: str,
    capabilities: dict[str, Any],
) -> str:
    """Resolve donor adapter README cites to TARGET authority paths."""

    if not _donor_adapter_read_superseded_by_target(path, capabilities):
        return path
    return TARGET_QUALITY_GATES_PATH


def _target_declared_adapter_ids(
    frontmatter: dict[str, Any],
    activation_row: dict[str, Any] | None,
) -> set[str]:
    """Union command frontmatter and activation registry adapter declarations."""

    declared: set[str] = set()
    for source in (
        frontmatter.get("required_adapters"),
        (activation_row or {}).get("adapters"),
    ):
        if isinstance(source, str):
            declared.add(source)
        elif isinstance(source, list):
            for item in source:
                if isinstance(item, str):
                    declared.add(item)
    return declared


def _normalize_cited_path(raw: str) -> str:
    """Strip markdown punctuation so ``path.md``.`` does not false-fail."""

    token = raw.strip().strip("`'\"")
    while token and token[-1] in "`.),;:":
        token = token[:-1]
    while token and token[0] in "(`":
        token = token[1:]
    return token


def _line_in_spans(line_index: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= line_index < end for start, end in spans)


def _extract_authority_paths(body: str) -> tuple[list[str], list[str]]:
    """Return (mandatory_paths, optional_paths) cited by command prose."""

    mandatory: list[str] = []
    optional: list[str] = []
    lines = body.splitlines()
    spans = _non_authority_spans(body)

    path_prefixes = (
        "Adapter (read):",
        "Adapter:",
        "Quality gates",
        "Quality gates (read):",
        "- Schema:",
        "Schema:",
    )

    for index, line in enumerate(lines):
        if _line_in_spans(index, spans):
            continue
        lowered = line.lower()
        is_optional = any(marker in lowered for marker in OPTIONAL_PATH_MARKERS)

        for prefix in path_prefixes:
            if prefix in line:
                fragment = line.split(prefix, 1)[1].strip().strip("`-")
                token = _normalize_cited_path(fragment.split()[0] if fragment else "")
                if token.startswith((".", "openspec/")):
                    (optional if is_optional else mandatory).append(token)

        for match in DECISION_PATH_PATTERN.finditer(line):
            # Placement decisions are use-time Architecture Decision Missing, not
            # install-time mandatory artifacts on TARGET.
            _ = match.group(0)

        for match in APPLY_SCHEMA_PATTERN.finditer(line):
            mandatory.append(_normalize_cited_path(match.group(0)))

        if "opsx/project/quality-gates.md" in line and "quality-gates" in lowered:
            if is_optional:
                optional.append("opsx/project/quality-gates.md")
            else:
                mandatory.append("opsx/project/quality-gates.md")

    return sorted(set(mandatory)), sorted(set(optional))


def _scan_command_body(
    command_id: str,
    body: str,
    frontmatter: dict[str, Any],
    capabilities: dict[str, Any],
    activation_row: dict[str, Any] | None = None,
) -> tuple[list[FailureRecord], DependencyInventoryRow]:
    """Analyze one command file for closure predicates."""

    failures: list[FailureRecord] = []
    spans = _non_authority_spans(body)
    lines = body.splitlines()
    command_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    inventory = DependencyInventoryRow(command_id=command_id, command_hash=command_hash)

    requires_config_check = False
    requires_openspec_guard = False
    for index, line in enumerate(lines):
        if CONFIG_CHECK_PATTERN.search(line) and not _line_in_spans(index, spans):
            requires_config_check = True
        if OPEN_SPEC_GUARD_PATH_PATTERN.search(line) and not _line_in_spans(index, spans):
            requires_openspec_guard = True
        if GUARD_PATTERN.search(line) and not _line_in_spans(index, spans):
            failures.append(
                FailureRecord(
                    command_id=command_id,
                    class_name="FOREIGN_DONOR_ASSUMPTION",
                    path="tools/openspec/openspec_guard.py",
                    scope="GLOBAL",
                    detail="unconditional Poetry guard precondition",
                )
            )
            inventory.foreign_assumptions.append("openspec_guard_poetry")

    declared_adapters = _target_declared_adapter_ids(frontmatter, activation_row)

    mandatory_paths, optional_paths = _extract_authority_paths(body)
    remapped_mandatory = sorted(
        {
            _remap_donor_adapter_mandatory_path(path, capabilities)
            for path in mandatory_paths
        }
    )
    inventory.mandatory_paths = remapped_mandatory
    inventory.optional_paths = optional_paths

    for path in mandatory_paths:
        if not (
            "poetry-python" in path
            or "python-poetry" in path
        ):
            continue
        if _donor_adapter_read_superseded_by_target(path, capabilities):
            continue
        if DONOR_PYTHON_ADAPTER_IDS.intersection(declared_adapters):
            continue
        failures.append(
            FailureRecord(
                command_id=command_id,
                class_name="FOREIGN_DONOR_ASSUMPTION",
                path=path,
                scope="GLOBAL",
                detail="adapter read without TARGET adapter declaration",
            )
        )
        inventory.foreign_assumptions.append(path)

    if requires_config_check:
        inventory.mandatory_paths = sorted(
            set(inventory.mandatory_paths + ["openspec/tools/check_config_contract.py"])
        )

    if requires_openspec_guard:
        inventory.mandatory_paths = sorted(
            set(inventory.mandatory_paths + ["tools/openspec/openspec_guard.py"])
        )

    inventory.status = "scanned"
    return failures, inventory


def _load_capabilities(root: Path) -> dict[str, Any]:
    import yaml

    path = root / "opsx" / "project" / "capabilities.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _capability_blocks(
    capabilities: dict[str, Any],
    command_id: str,
) -> list[FailureRecord]:
    unresolved = capabilities.get("unresolved_product_capabilities") or []
    if not isinstance(unresolved, list):
        return []
    blocks: list[FailureRecord] = []
    for capability_id in unresolved:
        if not isinstance(capability_id, str):
            continue
        command_ids = CAPABILITY_COMMAND_IDS.get(capability_id, frozenset())
        if command_id in command_ids:
            blocks.append(
                FailureRecord(
                    command_id=command_id,
                    class_name="TARGET_CAPABILITY_UNRESOLVED",
                    path=f"opsx/project/capabilities.yaml#{capability_id}",
                    scope="COMMAND",
                )
            )
    return blocks


def _validate_config_at_root(
    root: Path,
    global_failures: list[FailureRecord],
) -> str | None:
    config_path = root / "openspec" / "config.yaml"
    relative = "openspec/config.yaml"
    if not config_path.is_file():
        global_failures.append(
            FailureRecord(
                command_id="opsx-verify",
                class_name="MISSING_MANDATORY",
                path=relative,
                scope="GLOBAL",
            )
        )
        return None
    try:
        _ = load_and_validate_config(config_path)
    except ConfigContractError as error:
        global_failures.append(
            FailureRecord(
                command_id="opsx-verify",
                class_name=error.class_name,  # type: ignore[arg-type]
                path=relative,
                scope="GLOBAL",
                detail=str(error),
            )
        )
    checker_path = root / "openspec" / "tools" / "check_config_contract.py"
    if not checker_path.is_file():
        global_failures.append(
            FailureRecord(
                command_id="opsx-verify",
                class_name="MISSING_MANDATORY",
                path="openspec/tools/check_config_contract.py",
                scope="GLOBAL",
            )
        )
    return git_blob_sha256(root, relative)


def _validator_identity(root: Path) -> dict[str, str | None]:
    relative = "openspec/tools/check_config_contract.py"
    blob = git_blob_sha256(root, relative)
    if blob is None:
        return {"blob_sha256": None, "implementation_id": CHECKER_IMPLEMENTATION_ID}
    return {"blob_sha256": blob, "implementation_id": CHECKER_IMPLEMENTATION_ID}


def _activation_identity(rows: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for row in sorted(rows, key=lambda item: str(item.get("command_id"))):
        if row.get("active") is not True or row.get("installed") is not True:
            continue
        command_id = str(row.get("command_id"))
        command_hash = str(row.get("command_hash"))
        parts.append(f"{command_id}:{command_hash}")
    payload = "\n".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _compare_stored_pass_identity(
    root: Path,
    identity: dict[str, Any],
    global_failures: list[FailureRecord],
) -> None:
    """When a prior closure PASS receipt exists, config/checker/activation blobs must match."""

    receipt_path = root / "opsx" / "transfer" / "DEPENDENCY_CLOSURE_RECEIPT.json"
    if not receipt_path.is_file():
        return
    try:
        stored = json.loads(receipt_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    if stored.get("status") != "PASS":
        return
    stored_identity = stored.get("identity") or {}
    for key in (
        "activation_identity",
        "config_identity",
        "validator_identity",
        "capabilities_identity_yaml",
        "capabilities_identity_json",
        "closure_predicate_version",
    ):
        if stored_identity.get(key) != identity.get(key):
            global_failures.append(
                FailureRecord(
                    command_id="_closure_identity",
                    class_name="STALE_IDENTITY",
                    path=str(receipt_path.relative_to(root)),
                    scope="GLOBAL",
                    detail=f"identity field drift: {key}",
                )
            )
            return


def parse_transfer_manifest_rows(root: Path) -> list[tuple[str, str]]:
    """Разобрать markdown-ledger команд, сохраняя порядок строк (до dict collapse)."""

    manifest_path = root / TRANSFER_MANIFEST_REL
    if not manifest_path.is_file():
        return []
    body = manifest_path.read_text(encoding="utf-8")
    rows: list[tuple[str, str]] = []
    for line in body.splitlines():
        if not line.startswith("| `"):
            continue
        parts = [part.strip() for part in line.split("|")]
        if len(parts) < 3:
            continue
        command_cell = parts[1].strip("`")
        state_cell = parts[2].strip("`")
        if command_cell in {"command", "---"} or state_cell in {
            "terminal disposition",
            "---",
        }:
            continue
        rows.append((command_cell, state_cell))
    return rows


def _validate_installed_active_manifest_coverage(
    root: Path,
    activation_rows: list[dict[str, Any]],
    global_failures: list[FailureRecord],
) -> None:
    """Каждая installed-active строка ledger (кроме LEGACY_EXCLUDED) обязана иметь файл и activation."""

    manifest_path = root / TRANSFER_MANIFEST_REL
    if not manifest_path.is_file():
        return

    manifest_rows = parse_transfer_manifest_rows(root)
    if not manifest_rows:
        return

    command_ids = [command_id for command_id, _ in manifest_rows]
    if len(set(command_ids)) != len(command_ids):
        global_failures.append(
            FailureRecord(
                command_id="_transfer_manifest",
                class_name="STALE_IDENTITY",
                path=TRANSFER_MANIFEST_REL,
                scope="GLOBAL",
                detail="duplicate command rows in transfer manifest",
            )
        )

    acts = {
        str(row.get("command_id")): row
        for row in activation_rows
        if row.get("installed") is True and row.get("active") is True
    }
    commands_dir = root / ".cursor" / "commands"

    for command_id, state in manifest_rows:
        if state == "LEGACY_EXCLUDED":
            if command_id not in LEGACY_EXCLUDED_COMMAND_IDS:
                global_failures.append(
                    FailureRecord(
                        command_id=command_id,
                        class_name="STALE_IDENTITY",
                        path=TRANSFER_MANIFEST_REL,
                        scope="GLOBAL",
                        detail="unexpected LEGACY_EXCLUDED ledger row",
                    )
                )
            continue
        if state not in INSTALLED_ACTIVE_MANIFEST_STATES:
            continue
        command_path = commands_dir / f"{command_id}.md"
        if not command_path.is_file():
            global_failures.append(
                FailureRecord(
                    command_id=command_id,
                    class_name="MISSING_MANDATORY",
                    path=str(command_path.relative_to(root)),
                    scope="GLOBAL",
                    detail="installed-active ledger command file missing",
                )
            )
        if command_id not in acts:
            global_failures.append(
                FailureRecord(
                    command_id=command_id,
                    class_name="MISSING_MANDATORY",
                    path="opsx/registry/command_activation.ndjson",
                    scope="GLOBAL",
                    detail="installed-active ledger command missing activation row",
                )
            )


def evaluate_dependency_closure(root: Path) -> dict[str, Any]:
    """Run full dependency closure for ``root`` (read-only)."""

    global_failures: list[FailureRecord] = []
    command_blocks: list[FailureRecord] = []
    ignored: list[dict[str, str]] = []
    inventory: list[dict[str, Any]] = []

    activation_path = root / "opsx" / "registry" / "command_activation.ndjson"
    rows = read_activation_rows(activation_path)
    capabilities = _load_capabilities(root)

    target_revision = git_head_revision(root)
    config_identity = _validate_config_at_root(root, global_failures)
    validator_identity = _validator_identity(root)

    capabilities_yaml = git_blob_sha256(root, "opsx/project/capabilities.yaml")
    capabilities_json = git_blob_sha256(root, "opsx/project/capabilities.json")

    identity: dict[str, Any] = {
        "target_revision": target_revision,
        "activation_identity": _activation_identity(rows),
        "config_identity": config_identity,
        "validator_identity": validator_identity,
        "capabilities_identity_yaml": capabilities_yaml,
        "capabilities_identity_json": capabilities_json,
        "closure_predicate_version": CLOSURE_PREDICATE_VERSION,
        "openspec_cli_lock_identity": provider_identity(root),
        "surface_receipt_identity": git_blob_sha256(
            root, "opsx/transfer/FULL_SURFACE_MANIFEST.json"
        ),
    }

    projectization_path = root / "opsx" / "projectization.yaml"
    projectization_claimed = "UNKNOWN"
    normal_commands_enabled = False
    declared_predicate_version: str | None = None
    if projectization_path.is_file():
        import yaml

        proj = yaml.safe_load(projectization_path.read_text(encoding="utf-8")) or {}
        projectization_claimed = str(proj.get("status", "UNKNOWN"))
        normal_commands_enabled = proj.get("normal_commands_enabled") is True
        raw_predicate = proj.get("dependency_closure_predicate_version")
        if raw_predicate is not None:
            declared_predicate_version = str(raw_predicate)
        if declared_predicate_version != CLOSURE_PREDICATE_VERSION:
            global_failures.append(
                FailureRecord(
                    command_id="_projectization",
                    class_name="STALE_IDENTITY",
                    path="opsx/projectization.yaml",
                    scope="GLOBAL",
                    detail=(
                        "dependency_closure_predicate_version drift: "
                        f"yaml={declared_predicate_version!r} "
                        f"evaluator={CLOSURE_PREDICATE_VERSION!r}"
                    ),
                )
            )

    for provider_result in validate_openspec_cli_provider(root):
        if provider_result.ok:
            continue
        global_failures.append(
            FailureRecord(
                command_id="_openspec_cli",
                class_name="MISSING_MANDATORY",
                path="opsx_tools/openspec-cli-node",
                scope="GLOBAL",
                detail=f"{provider_result.code}: {provider_result.detail}",
            )
        )

    commands_dir = root / ".cursor" / "commands"

    _validate_installed_active_manifest_coverage(root, rows, global_failures)

    for row in rows:
        command_id = str(row.get("command_id"))
        if row.get("active") is not True or row.get("installed") is not True:
            ignored.append(
                {
                    "command_id": command_id,
                    "class": "DISABLED",
                    "path": str(row.get("target_path", "")),
                }
            )
            continue
        if command_id in LEGACY_EXCLUDED_COMMAND_IDS:
            global_failures.append(
                FailureRecord(
                    command_id=command_id,
                    class_name="STALE_IDENTITY",
                    path=str(row.get("target_path", "")),
                    scope="GLOBAL",
                    detail="LEGACY_EXCLUDED command must not remain activated",
                )
            )
            continue

        command_path = commands_dir / f"{command_id}.md"
        if not command_path.is_file():
            global_failures.append(
                FailureRecord(
                    command_id=command_id,
                    class_name="MISSING_MANDATORY",
                    path=str(command_path.relative_to(root)),
                    scope="GLOBAL",
                )
            )
            continue

        body = command_path.read_text(encoding="utf-8")
        frontmatter = _parse_frontmatter(body)
        scan_failures, inv_row = _scan_command_body(
            command_id,
            body,
            frontmatter,
            capabilities,
            activation_row=row,
        )
        for failure in scan_failures:
            if failure.scope == "GLOBAL":
                global_failures.append(failure)
            else:
                command_blocks.append(failure)

        for path in inv_row.mandatory_paths:
            resolved = root / path
            if not resolved.is_file():
                global_failures.append(
                    FailureRecord(
                        command_id=command_id,
                        class_name="MISSING_MANDATORY",
                        path=path,
                        scope="GLOBAL",
                    )
                )
                inv_row.status = "missing_mandatory"

        for block in _capability_blocks(capabilities, command_id):
            command_blocks.append(block)
            inv_row.capability_gates.append(block.path.split("#", 1)[-1])

        relative_command = command_path.relative_to(root).as_posix()
        current_hash = git_blob_sha256(root, relative_command)
        if current_hash is None:
            current_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if (
            command_id not in DEFERRED_ACTIVATION_HASH_DRIFT_COMMANDS
            and row.get("command_hash") != current_hash
        ):
            global_failures.append(
                FailureRecord(
                    command_id=command_id,
                    class_name="STALE_IDENTITY",
                    path=str(command_path.relative_to(root)),
                    scope="GLOBAL",
                    detail="activation command_hash drift",
                )
            )

        inventory.append(inv_row.as_dict())

    _compare_stored_pass_identity(root, identity, global_failures)

    # Distinct global failures by tuple key
    seen: set[tuple[str, str, str]] = set()
    unique_global: list[FailureRecord] = []
    for item in global_failures:
        key = (item.command_id, item.class_name, item.path)
        if key in seen:
            continue
        seen.add(key)
        unique_global.append(item)

    blocking_foreign = any(
        item.class_name == "FOREIGN_DONOR_ASSUMPTION" for item in unique_global
    )
    blocking_missing = any(
        item.class_name == "MISSING_MANDATORY" for item in unique_global
    )
    closure_blocks_projectization = blocking_foreign or blocking_missing
    if unique_global and (
        projectization_claimed == "COMPLETE"
        or normal_commands_enabled
        or (
            closure_blocks_projectization
            and projectization_claimed
            not in ("CLOSURE_BLOCKED", "INCOMPLETE", "UNKNOWN")
        )
    ):
        false_complete = FailureRecord(
            command_id="_projectization",
            class_name="FALSE_COMPLETE_CLAIM",
            path="opsx/projectization.yaml",
            scope="GLOBAL",
            detail=(
                "projectization claim incompatible with closure global failures "
                f"(blocking_foreign={blocking_foreign}, "
                f"blocking_missing={blocking_missing})"
            ),
        )
        key = (
            false_complete.command_id,
            false_complete.class_name,
            false_complete.path,
        )
        if key not in seen:
            unique_global.append(false_complete)

    status = "PASS" if not unique_global else "FAIL"
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "target_repo": TARGET_REPO,
        "target_revision": target_revision,
        "projectization_status_claimed": projectization_claimed,
        "normal_commands_enabled": normal_commands_enabled,
        "identity": identity,
        "architecture_decision_gaps": list(ARCHITECTURE_DECISION_GAPS),
        "dependency_inventory": inventory,
        "global_failures": [item.as_dict() for item in unique_global],
        "command_blocks": [item.as_dict() for item in command_blocks],
        "ignored": ignored,
    }
    return result


def run_closure_cli(root: Path) -> int:
    """Evaluate closure and print JSON to stdout; exit 0 only on PASS."""

    result = evaluate_dependency_closure(root)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1
