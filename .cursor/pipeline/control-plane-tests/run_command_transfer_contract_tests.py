"""Проверки активированного пакета переноса OPSX-команд.

Модуль проверяет статические интерфейсные и wiring-контракты перенесённых
команд без запуска продуктового кода или внешних систем.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, cast

import sys

_ROOT_FOR_IMPORT = Path(__file__).resolve().parents[3]
if str(_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(_ROOT_FOR_IMPORT))

from opsx_dependency_closure import (
    DEFERRED_ACTIVATION_HASH_DRIFT_COMMANDS,
    evaluate_dependency_closure,
)


ROOT = _ROOT_FOR_IMPORT
COMMANDS = ROOT / ".cursor" / "commands"
ACTIVATION = ROOT / "opsx" / "registry" / "command_activation.ndjson"
PROVENANCE = ROOT / "opsx" / "registry" / "provenance.ndjson"
ORIENTATION = ROOT / "opsx" / "shared" / "meta-orientation.md"
META_ROUTER = COMMANDS / "opsx-meta.md"
TRANSFER_MANIFEST = ROOT / "opsx" / "registry" / "command_transfer_manifest.md"
FULL_SURFACE_MANIFEST = ROOT / "opsx" / "transfer" / "FULL_SURFACE_MANIFEST.json"

TRANSFERRED_COMMANDS: dict[str, dict[str, tuple[str, ...] | str]] = {
    "opsx-meta-analyze-pipeline": {
        "contract_id": "OPSX-WB-META-ANALYZE-PIPELINE-01",
        "required": (
            "$ARGUMENTS",
            "EXAMPLE_CHAT:<id>",
            "CHAT_ID:<id>",
            "TOPIC_INTENT:",
            "ROLE_MATRIX:",
            "HANDOFF_CREATE:",
            "HANDOFF_FIX:",
            "HANDOFF_CONSULT:",
            "HANDOFF_PIPELINE:",
            "READ_ONLY",
        ),
    },
    "opsx-meta-promote-process-lesson": {
        "contract_id": "OPSX-WB-META-PROMOTE-PROCESS-LESSON-01",
        "required": (
            "$ARGUMENTS",
            "EXTEND_EXISTING",
            "DESIGN_BRIEF_READY",
            "SHARED_SURFACES_TO_UPDATE:",
            "FIX_PLAN_HINT:",
            "AUTHORIZE_META_FIX_SESSION",
            "READ_ONLY",
        ),
    },
    "opsx-plan": {
        "contract_id": "OPSX-WB-PLAN-BRIDGE-01",
        "required": (
            "$ARGUMENTS",
            "/opsx-new",
            "/opsx-ff",
            "/opsx-continue",
            "/opsx-update",
            "BLOCKED_NEEDS_CHANGE",
            "READ_ONLY",
        ),
    },
    "opsx-estimate": {
        "contract_id": "OPSX-WB-ESTIMATE-01",
        "required": (
            "$ARGUMENTS",
            "MODE=FORECAST",
            "MODE=ACTUALS",
            "planningHome.changesDir",
            "openspec/changes/archive",
            "PRIOR_EMPTY",
            "LEARNING_UNAVAILABLE",
            "BLOCKED_MISSING_SPECS",
            "NOT_VERIFY_GO: true",
            "NOT_ARCHIVE_AUTHORITY: true",
            "COMMIT_ALLOWED: NO",
            "PRODUCTION_WRITES_ALLOWED: NO",
        ),
    },
    "opsx-rename-change": {
        "contract_id": "OPSX-WB-RENAME-CHANGE-01",
        "required": (
            "$ARGUMENTS",
            "planningHome.changesDir",
            "BLOCKED_NEEDS_CHOICE",
            "BLOCKED_PATH_AUTHORITY",
            'git mv -- "<resolved-old-change-root>" "<resolved-new-change-root>"',
            "RENAMED_NEEDS_EVOLUTION_RETARGET",
        ),
    },
    "opsx-doctor": {
        "contract_id": "OPSX-WB-DOCTOR-01",
        "required": (
            "$ARGUMENTS",
            "openspec doctor --json",
            "openspec list --json",
            'openspec status --change "<name>" --json',
            "planningHome.changesDir",
            "<planningHome.changesDir>/archive",
            "CORRUPT_LEFTOVER_ACTIVE_CHANGE",
            "CORRUPT_OR_EMPTY_PLANNING_ARTIFACTS",
            "GO_UNSUPPORTED",
            "GO_SUPPORTED: false",
            "READ_ONLY: true",
        ),
    },
    "opsx-bug-intake-clear": {
        "contract_id": "OPSX-WB-BUG-INTAKE-CLEAR-01",
        "required": (
            "$ARGUMENTS",
            "SOURCE_PARITY: READ_ONLY_SUBSET",
            "CASE_LEDGER_WRITER: /opsx-materialize-bug-queue",
            "HISTORICAL_CASE_MAP: PRESENT | ABSENT | NOT_REQUESTED",
            "PROCESS_CONTRACT",
            "CLEAN_FINDINGS",
            "relation=duplicate_intake",
            "GO_SUPPORTED: false",
        ),
    },
    "opsx-bug-family-plan": {
        "contract_id": "OPSX-WB-BUG-FAMILY-PLAN-01",
        "required": (
            "$ARGUMENTS",
            "SOURCE_PARITY: NON_DURABLE_PLANNING_SUBSET",
            "TARGET_FAMILY_WRITER: /opsx-bug-team",
            "WORK_ITEM_TYPE: BUG_FAMILY | ATOMIC_BUG",
            "FAMILY_KIND: PRE_REPAIR_ANALYSIS_ONLY",
            "PERSISTENCE: NONE",
            "TARGET_ROUTE_HANDOFFS:",
            "GO_SUPPORTED: false",
        ),
    },
    "opsx-bug-disposition": {
        "contract_id": "OPSX-WB-BUG-DISPOSITION-01",
        "required": (
            "$ARGUMENTS",
            "SOURCE_PARITY: ROUTING_ONLY",
            "CURRENT_CHANGE_DISPOSITION: NOT_ADJUDICATED_HERE",
            "CLASSIFICATION_OWNER: /opsx-classify-bug",
            "REPAIR_OWNER: /opsx-bug-team",
            "BLOCKED_ROUTE_MISMATCH",
            "CLAIM_REPAIR_REQUIRED",
            "GO_SUPPORTED: false",
        ),
    },
    "opsx-intent-align": {
        "contract_id": "OPSX-WB-INTENT-ALIGN-01",
        "required": (
            "$ARGUMENTS",
            "OWNER_CLARIFICATION_REQUIRED",
            "OWNER_CONFIRMATION_REQUIRED",
            "MATERIAL_AMBIGUITY",
            "PRIOR_ALIGNMENT_CHALLENGE",
            "SYSTEM_RESTATEMENT_SHA256",
            "TECHNICAL_DECISIONS_MADE: NONE",
            "AMBIGUOUS_CONFIRMATION_TOKEN",
            "INTENT_ALIGNMENT_STALE",
            "INTENT_CONSUMER_INTEGRATION: MISSING",
            "DOWNSTREAM_RECEIPT_ENFORCEMENT: NOT_WIRED",
        ),
    },
    "opsx-ts-design": {
        "contract_id": "OPSX-WB-TS-DESIGN-01",
        "required": (
            "$ARGUMENTS",
            "mode=draft|reconcile",
            "TS_DESIGN_SHA256",
            "INPUT_REVISION_STALE",
            "UPSTREAM_SPECS_MISSING",
            "UPSTREAM_SEMANTIC_REALIGNMENT_REQUIRED",
            "EVIDENCE_GAP",
            "OWNER_CONTRACT_WRITER: NO",
            "DESIGN_MD_WRITER: NO",
            "REVIEW_STATE_WRITER: NO",
            "TS_REVIEW_ACTIVATED: NO",
            "SOURCE_PARITY: PARTIAL",
            "PLANNING_RISK_BIND: NOT_REQUIRED",
            "CONSUMER_INTEGRATION: MISSING",
            "IMPLEMENTED_UNVERIFIED",
            ".cursor/pipeline/technical-strategy/technical_strategy.py",
            ".cursor/pipeline/technical-strategy-tests/test_ts_design.py",
        ),
    },
    "opsx-ts-review": {
        "contract_id": "OPSX-WB-TS-REVIEW-01",
        "required": (
            "$ARGUMENTS",
            "REVIEW_STATE: PASS | DEFECTS_FOUND | BLOCKED",
            "BOUND_TS_DESIGN_SHA256",
            "INDEPENDENT_REVIEW_REQUIRED",
            "UNTRUSTED_PASS_CLAIM",
            "REVIEWER_NOT_INDEPENDENT",
            "CROSS_REVISION_REUSE",
            "INPUT_REVISION_STALE",
            "OWNER_DECISION",
            "DESIGN_UNLOCK: NOT_WIRED",
            "CONSUMER_INTEGRATION: MISSING",
            "SOURCE_PARITY: PARTIAL",
            "IMPLEMENTED_UNVERIFIED",
            "STOP_DESIGN_UNLOCK_NOT_WIRED",
            "TS_DESIGN_WRITER: NO",
            "DESIGN_MD_WRITER: NO",
            ".cursor/pipeline/technical-strategy/technical_strategy.py",
            ".cursor/pipeline/technical-strategy-tests/test_ts_review.py",
            ".cursor/pipeline/technical-strategy/ts_review.schema.json",
        ),
    },
    "opsx-risk": {
        "contract_id": "OPSX-WB-RISK-01",
        "required": (
            "$ARGUMENTS",
            "SUPPORTED_MODES: planning | tasking",
            "CHECK_SELECTOR: NO",
            "VERIFICATION_MAP_WRITER: NO",
            "GREEN_WRITER: NO",
            "TASK_WRITER: NO",
            "SOURCE_PARITY: PARTIAL",
            "OCR_PLANNING_INPUT: SAME_TURN_PASTED_PASS_REQUIRED",
            "SOURCE_OWNER_CONTRACT_SHA256",
            "SOURCE_PLANNING_RISK_SHA256",
            "SOURCE_PLANNING_INPUTS",
            "TASKING OUTPUT FIREWALL",
            "CONSUMER_INTEGRATION: PARTIAL",
        ),
    },
    "opsx-run-typecheck": {
        "contract_id": "OPSX-WB-RUN-TYPECHECK-01",
        "required": (
            "$ARGUMENTS",
            "mode=bounded",
            "ATOMIC_EXECUTOR",
            "FIX_TYPES: NO",
            "GREEN_WRITER: NO",
            "TYPECHECK_ELIGIBLE: pyright-strict | mypy",
            "BINDING_GAP",
            "ASSIGNMENT_NOT_CURRENT",
            "/bounded-executor",
            "bounded_verify_executor.py",
            "/opsx-fix-types",
            "/opsx-static-check",
            "SOURCE_PARITY: PARTIAL",
            "IMPLEMENTED_UNVERIFIED",
            "typecheck_step=",
        ),
    },
    "opsx-run-build": {
        "contract_id": "OPSX-WB-RUN-BUILD-01",
        "required": (
            "$ARGUMENTS",
            "build_recipe=poetry_package",
            "output_dir=",
            "mode=bounded",
            "BINDING_GAP",
            "S6_PACKAGE_BUILD_CAPABILITY_UNREGISTERED",
            "EXECUTOR_ADAPTER_GAP",
            "OUTPUT_DIR_REQUIRED",
            "ATOMIC_EXECUTOR",
            "GREEN_WRITER: NO",
            "LEDGER_WRITER: NO",
            "RESULT_WRITER: NO",
            "DURABLE_RECEIPT: NONE",
            "RUN_RESULT: chat-only",
            "/bounded-executor",
            ".cursor/pipeline/run-build/run_build.py",
            ".cursor/pipeline/run-build-tests/test_run_build_manual.py",
            "SOURCE_PARITY: PARTIAL",
            "IMPLEMENTED_UNVERIFIED",
        ),
    },
    "opsx-run-tests": {
        "contract_id": "OPSX-WB-RUN-TESTS-01",
        "required": (
            "$ARGUMENTS",
            "mode=manual",
            "lane=pytest",
            "mode=bounded",
            "BINDING_GAP",
            "S6_TEST_CAPABILITY_UNREGISTERED",
            "ATOMIC_EXECUTOR",
            "GREEN_WRITER: NO",
            "TEST_AUTHORING: NO",
            "LEDGER_WRITER: NO",
            "RESULT_WRITER: NO",
            "DURABLE_RECEIPT: NONE",
            "DESTRUCTIVE_LOCAL_BLOCKED",
            "RUN_RESULT: chat-only",
            "/bounded-executor",
            ".cursor/pipeline/run-tests/run_tests.py",
            ".cursor/pipeline/run-tests-tests/test_run_tests_manual.py",
            "SOURCE_PARITY: PARTIAL",
            "IMPLEMENTED_UNVERIFIED",
        ),
    },
    "opsx-browser-check": {
        "contract_id": "OPSX-WB-BROWSER-CHECK-01",
        "required": (
            "$ARGUMENTS",
            "IMPLEMENTATION_STATUS: FUNCTIONAL_PROVEN",
            "SOURCE_PARITY: ADAPTED",
            "BROWSER_ADAPTER_DECLARED: NO",
            "NOT_APPLICABLE",
            "BROWSER_SURFACE_UNDECLARED",
            "BINDING_GAP",
            "S6_SMOKE_ACCEPTANCE_UNREGISTERED",
            "stale_revision",
            ".cursor/pipeline/browser-check/browser_check.py",
            ".cursor/pipeline/browser-check-tests/test_browser_check.py",
            "GREEN_WRITER: NO",
            "COMMIT_ALLOWED: NO",
        ),
    },
    "opsx-control-plane-security-review": {
        "contract_id": "OPSX-WB-CONTROL-PLANE-SECURITY-REVIEW-01",
        "required": (
            "$ARGUMENTS",
            "IMPLEMENTATION_STATUS: FUNCTIONAL_PROVEN",
            "CONTROL_PLANE_SECURITY_VERDICT_AUTHORITY: YES",
            "PRODUCT_SECURITY_VERDICT_AUTHORITY: NO",
            "PROCESS_WRITE_AUTHORITY: NO",
            "self_certification_forbidden",
            "stale_revision",
            "path_outside_workspace",
            "STILL_VULNERABLE",
            "REMEDIATED",
            "opsx/runs/security-cases/",
            "reviews/control-plane",
            ".cursor/pipeline/control-plane-security-review/control_plane_security_review.py",
            ".cursor/pipeline/control-plane-security-review-tests/test_control_plane_security_review.py",
            "GREEN_WRITER: NO",
            "ARCHIVE_VERDICT_WRITER: NO",
        ),
    },
    "opsx-post-deploy-acceptance": {
        "contract_id": "OPSX-WB-POST-DEPLOY-ACCEPTANCE-01",
        "required": (
            "$ARGUMENTS",
            "IMPLEMENTATION_STATUS: FUNCTIONAL_PROVEN",
            "PRODUCTION_WRITES: NO",
            "deploy_receipt=",
            "opsx/runs/devops/",
            "PRODUCTION_MUTATION_FORBIDDEN",
            "URL_NOT_ALLOWLISTED",
            "acceptance_probes_missing",
            "AUTHORITY: /opsx-devops",
            ".cursor/pipeline/post-deploy-acceptance/post_deploy_acceptance.py",
            ".cursor/pipeline/post-deploy-acceptance-tests/test_post_deploy_acceptance.py",
            "GREEN_WRITER: NO",
        ),
    },
    "opsx-security-learn": {
        "contract_id": "OPSX-WB-SECURITY-LEARN-01",
        "required": (
            "$ARGUMENTS",
            "IMPLEMENTATION_STATUS: FUNCTIONAL_PROVEN",
            "SECURITY_LEARNING_AUTHORITY: YES",
            "SECURITY_REVIEW_VERDICT_AUTHORITY: NO",
            "FINDING_DISPOSITION=REMEDIATED",
            "INCIDENT_SPECIFIC_NOT_TRANSFERABLE",
            "BLOCKED_AUTHORITY_GAP",
            "opsx/runs/security-learning/",
            ".cursor/pipeline/security-learn/security_learn.py",
            ".cursor/pipeline/security-learn-tests/test_security_learn.py",
            "GREEN_WRITER: NO",
        ),
    },
    "opsx-security-review": {
        "contract_id": "OPSX-WB-SECURITY-REVIEW-01",
        "required": (
            "$ARGUMENTS",
            "IMPLEMENTATION_STATUS: IMPLEMENTED_UNVERIFIED",
            "RUNTIME_STATUS: ACTIVE_WITH_LIMITS",
            "SOURCE_PARITY: PARTIAL",
            "SECURITY_REVIEW_DECLARED: NO",
            "CAPABILITY_PARITY: PARTIAL",
            "mode=report",
            "mode=bounded",
            "BINDING_GAP",
            "S6_SECURITY_REVIEW_CAPABILITY_UNREGISTERED",
            "S6_SECURITY_REVIEW_RESULT_WRITER_NOT_INSTALLED",
            "no fallback",
            "ARCHIVE_VERDICT: not_issued",
            "PRODUCT_SECURITY_VERDICT: not_issued",
            "DURABLE_RECEIPT: NONE",
            "VERIFY_CONSUMES_SECURITY_REVIEW: NO",
            "FINDING_DISPOSITION: REMEDIATED | STILL_OPEN | NOT_REPRODUCED | DATA_GAP | OUT_OF_SCOPE",
            "FORBIDDEN_ARCHIVE_STATUS: PASS | PASS_WITH_RISK | NOT_APPLICABLE",
            "undeclared_surface_coverage",
            "stale_revision",
            "revision_not_current",
            "path_outside_workspace",
            "index_scan_required",
            "duplicate_signal_hash",
            ".cursor/pipeline/security-review/security_review.py",
            ".cursor/pipeline/security-review-tests/test_security_review.py",
            "GREEN_WRITER: NO",
            "ARCHIVE_VERDICT_WRITER: NO",
            "COMMIT_ALLOWED: NO",
            "CONTROL_PLANE_SECURITY_VERDICT_AUTHORITY: NO",
            "INFRA_SECURITY_VERDICT_AUTHORITY: NO",
        ),
    },
    "opsx-security-triage": {
        "contract_id": "OPSX-WB-SECURITY-TRIAGE-01",
        "required": (
            "$ARGUMENTS",
            "IMPLEMENTATION_STATUS: IMPLEMENTED_UNVERIFIED",
            "RUNTIME_STATUS: ACTIVE_WITH_LIMITS",
            "CAPABILITY_PARITY: PARTIAL",
            "SECURITY_TRIAGE_DECLARED: NO",
            "LOCAL_EVIDENCE_PATH: bounded_workspace_citation",
            "SOURCE_PARITY: PARTIAL",
            "SECURITY_FINDING_PACKET",
            "opsx/runs/security-cases/",
            "path_outside_workspace",
            "plane_misroute",
            "ABSENCE_IS_NOT_A_DISPOSITION",
            "CONFIDENCE_IS_PROOF: NO",
            "BUG_LANE_OWNER: NO",
            "SECURITY_REVIEW_VERDICT_AUTHORITY: NO",
            "COMMIT_ALLOWED: NO",
            ".cursor/pipeline/security-triage/security_triage.py",
            ".cursor/pipeline/security-triage-tests/test_security_triage.py",
            ".cursor/pipeline/security-triage/SECURITY_FINDING_PACKET.schema.json",
            "AFFECTED_PLANE: PRODUCT | CONTROL_PLANE | INFRASTRUCTURE",
            "STATUS: CONFIRMED | FALSE_POSITIVE | NEEDS_CONTEXT | OUT_OF_SCOPE | DUPLICATE | BLOCKED",
        ),
    },
    "opsx-meta-add-command": {
        "contract_id": "OPSX-WB-META-ADD-COMMAND-01",
        "required": (
            "$ARGUMENTS",
            "COMMAND: /opsx-<name>",
            "PURPOSE:",
            "/opsx-meta-design-command",
            "/opsx-meta-import-command",
            "/opsx-meta-fix-command-defects",
            "SOURCE_PARITY: PARTIAL",
            "IMPLEMENTED_UNVERIFIED",
            "AUTONOMOUS_DELIVERY_TERMINAL: BLOCKED",
            "NOT_COMMITTED_THIS_RUN",
            "REGISTRY_WRITER: NO",
            "META_COMMIT_INVOKER: NO",
            "OPSX_COMMAND_DELIVERY_GREEN",
            "READ_ONLY",
            "ROUTE_CLASS:",
        ),
    },
    "opsx-meta-update-all-snapshots": {
        "contract_id": "OPSX-WB-META-SNAPSHOT-ALL-01",
        "required": (
            "$ARGUMENTS",
            "SOURCE.yaml",
            "CENTRAL_ROOT",
            "TOOL_CENTRAL_OVERRIDE: missing",
            "SNAPSHOT_CENTRAL_OVERRIDE_MISSING",
            "SOURCE_PARITY: PARTIAL",
            "GO_SUPPORTED: false",
            "snapshot-plan",
            "Never run `snapshot-apply` on TARGET from this command",
            "/opsx-meta-update-snapshot",
            "/opsx-meta-fix-command-defects",
        ),
    },
    "opsx-orch-bugbot-converge": {
        "contract_id": "OPSX-WB-ORCH-BUGBOT-CONVERGE-01",
        "required": (
            "$ARGUMENTS",
            "against=<target>",
            "CHECK_COMMAND: /review-bugbot",
            "python tools/temporal/runtime.py run bugbot",
            "python tools/temporal/orch_target.py converge-plan",
            "BUGBOT_CONVERGED",
            "BLOCKED_NO_PROGRESS",
            "PROCESS_HANDOFF",
            "WRAPPER_PRODUCT_WRITE: NO",
            "GIT_COMMIT: FORBIDDEN",
            "/opsx-orch-bug-fix",
            "/opsx-bug-team",
            "/opsx-meta-fix-command-defects",
            "SOURCE_PARITY: PARTIAL",
            "FUNCTIONAL_PROVEN",
            "IMPLEMENTATION_STATUS: FUNCTIONAL_PROVEN",
            "CHECK_TRANSPORT: TEMPORAL_CURSOR_SDK",
            "OWNER_GO_AFTER_INVOCATION_REQUIRED: NO",
            "REPAIR_PRODUCT: /opsx-orch-bug-fix",
            "SUPERVISOR_REPLACEMENT: NO",
            ".cursor/pipeline/orch-bugbot-converge/orch_bugbot_converge.py",
            ".cursor/pipeline/orch-bugbot-converge-tests/test_orch_bugbot_converge.py",
        ),
    },
    "opsx-orch-bug-fix": {
        "contract_id": "OPSX-WB-ORCH-BUG-FIX-01",
        "required": (
            "$ARGUMENTS",
            "START_TEMPORAL: FORBIDDEN",
            "WRAPPER_PRODUCT_WRITE: NO",
            "GIT_COMMIT: FORBIDDEN",
            "REPAIRED_THIS_BATCH: YES | NO",
            "BUG_BATCH_CLEAR",
            "/opsx-bug-team",
            "/opsx-bug-intake-clear",
            "/opsx-materialize-bug-queue",
            "/opsx-meta-fix-command-defects",
            "BLOCKED_NO_PROGRESS",
            "SOURCE_PARITY: PARTIAL",
            "FUNCTIONAL_PROVEN",
            "IMPLEMENTATION_STATUS: FUNCTIONAL_PROVEN",
            "python tools/temporal/orch_target.py bug-fix-plan",
            "TRANSPORT: IN_PARENT_SYNC",
            "TEMPORAL_NESTED_START: NO",
            "UNSUPPORTED_UPSTREAM_GO",
            "SUPERVISOR_REPLACEMENT: NO",
            ".cursor/pipeline/orch-bug-fix/orch_bug_fix.py",
            ".cursor/pipeline/orch-bug-fix-tests/test_orch_bug_fix.py",
        ),
    },
    "opsx-models": {
        "contract_id": "OPSX-WB-MODELS-01",
        "required": (
            "$ARGUMENTS",
            "IMPLEMENTATION_STATUS: IMPLEMENTED",
            "RUNTIME_STATUS: ACTIVE",
            "SOURCE_PARITY: ADAPTED",
            "GO_SUPPORTED: true",
            "READ_ONLY: true (default)",
            "TEMPORAL_EFFECTIVE_ROUTING: AVAILABLE",
            "TRANSPORT: CURSOR_PYTHON_SDK",
            "tools/temporal/model_config.py",
            "tools/temporal/agent-models.json",
            "scope=effective|project|runtime",
            "EXECUTION_MODEL_AUTHORITY:",
            "MODEL_CONFIG_APPLIED",
        ),
    },
}

META_ROUTED_COMMANDS = {
    "opsx-meta-analyze-pipeline",
    "opsx-meta-promote-process-lesson",
}

LEGACY_EXCLUDED_COMMAND_IDS = frozenset({"opsx-bugbot-report"})

ACTIVE_BUGBOT_ROUTES = frozenset(
    {
        "opsx-orch-bugbot-converge",
        "opsx-bug-intake-clear",
    }
)

FORBIDDEN_TARGET_ASSUMPTIONS = (
    'required_adapters: ["opsx-lite", "vue-npm"]',
    "npm run build",
    "tiriel.ru",
    "openspec/archive/",
    "OPSX-GAME-ESTIMATE-01",
)

ALLOWED_TRANSFER_STATES = {
    "EXISTING_ADAPTATION_REQUIRED",
    "IMPLEMENTED_UNVERIFIED",
    "FUNCTIONAL_PROVEN",
    "TARGET_NATIVE_RETAINED",
    "TARGET_NATIVE_RECONCILED",
    "LEGACY_EXCLUDED",
    "SOURCE_STAGED_MIGRATION_HOLD",
    "BLOCKED_CONTRACT_CONFLICT",
    "NOT_STARTED",
    "ACTIVE_WITH_CAPABILITY_GATES",
    "ACTIVE_CORE",
}

INSTALLED_ACTIVE_RUNTIME_CLASSES = frozenset(
    {
        "ACTIVE",
        "ACTIVE_WITH_LIMITS",
        "ACTIVE_WITH_CAPABILITY_GATES",
        "ACTIVE_CORE",
    }
)

INSTALLED_ACTIVE_MANIFEST_STATES = frozenset(
    {
        "IMPLEMENTED_UNVERIFIED",
        "FUNCTIONAL_PROVEN",
        "ACTIVE_WITH_CAPABILITY_GATES",
        "ACTIVE_CORE",
    }
)

_TRANSFER_BATCH_SOURCE_COMMIT = "91311ada29721cfb9aab7a00e1dc4d2a0a8d1982"


def read_activation_rows() -> list[dict[str, object]]:
    """Прочитать activation registry и проверить валидность каждой JSON-строки."""

    rows: list[dict[str, object]] = []
    for line_number, raw_line in enumerate(
        ACTIVATION.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not raw_line.strip():
            continue
        try:
            value = json.loads(raw_line)
        except json.JSONDecodeError as error:
            raise AssertionError(
                f"{ACTIVATION}:{line_number}: invalid JSON: {error}"
            ) from error
        if not isinstance(value, dict):
            raise AssertionError(f"{ACTIVATION}:{line_number}: row is not an object")
        rows.append(cast(dict[str, object], value))
    return rows


def parse_command_transfer_manifest_rows() -> list[tuple[str, str]]:
    """Разобрать markdown-ledger команд, сохраняя порядок строк до dict collapse."""

    body = TRANSFER_MANIFEST.read_text(encoding="utf-8")
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


def parse_command_transfer_manifest() -> dict[str, str]:
    """Разобрать markdown-ledger команд в map command_id → terminal disposition."""

    return dict(parse_command_transfer_manifest_rows())


def require_installed_active_activation(
    command_id: str,
    *,
    command_hash: str | None = None,
) -> dict[str, object]:
    """Проверить, что команда зарегистрирована как installed-active."""

    activation = next(
        row for row in read_activation_rows() if row.get("command_id") == command_id
    )
    if activation.get("runtime_class") not in INSTALLED_ACTIVE_RUNTIME_CLASSES:
        raise AssertionError(
            f"{ACTIVATION}: {command_id} must be installed-active, "
            f"got {activation.get('runtime_class')!r}"
        )
    if activation.get("installed") is not True:
        raise AssertionError(f"{ACTIVATION}: {command_id} is not installed")
    if command_hash is not None and activation.get("command_hash") != command_hash:
        raise AssertionError(f"{ACTIVATION}: {command_id} command_hash is stale")
    return activation


def verify_mandatory_transfer_batch_provenance(
    *,
    commands: list[str],
    command_hashes: list[str],
    label: str,
    integration_status: str | None = None,
    design_unlock_wired: bool | None = None,
) -> None:
    """Сверить обязательное provenance batch-событие (fail-closed при отсутствии)."""

    provenance_rows = [
        json.loads(raw_line)
        for raw_line in PROVENANCE.read_text(encoding="utf-8").splitlines()
        if raw_line.strip()
    ]
    matching_events = [
        row
        for row in provenance_rows
        if row.get("event") == "full_command_transfer_batch"
        and row.get("commands") == commands
    ]
    if len(matching_events) != 1:
        raise AssertionError(
            f"{PROVENANCE}: expected one {label} transfer event, "
            f"got {len(matching_events)}"
        )
    event = matching_events[0]
    if event.get("command_hashes") != command_hashes:
        raise AssertionError(f"{PROVENANCE}: {label} command hash is stale")
    if event.get("source_commit") != _TRANSFER_BATCH_SOURCE_COMMIT:
        raise AssertionError(f"{PROVENANCE}: {label} source commit drift")
    if integration_status is not None and event.get("integration_status") != integration_status:
        raise AssertionError(
            f"{PROVENANCE}: {label} integration_status must be {integration_status!r}"
        )
    if design_unlock_wired is not None and event.get("design_unlock_wired") != design_unlock_wired:
        raise AssertionError(
            f"{PROVENANCE}: {label} design_unlock_wired must be {design_unlock_wired!r}"
        )


def check_command(command_id: str, contract: dict[str, tuple[str, ...] | str]) -> None:
    """Проверить один перенесённый command-файл и его обязательный интерфейс."""

    path = COMMANDS / f"{command_id}.md"
    if not path.is_file():
        raise AssertionError(f"missing command: {path}")
    body = path.read_text(encoding="utf-8")
    if not body.startswith("---\n"):
        raise AssertionError(f"{path}: missing YAML frontmatter")
    expected_contract = f"contract_id: {contract['contract_id']}"
    if expected_contract not in body:
        raise AssertionError(f"{path}: missing {expected_contract}")
    if f"# /{command_id}" not in body:
        raise AssertionError(f"{path}: command heading does not match filename")
    for token in contract["required"]:
        if token not in body:
            raise AssertionError(f"{path}: missing interface token {token!r}")
    for token in FORBIDDEN_TARGET_ASSUMPTIONS:
        if token in body:
            raise AssertionError(f"{path}: forbidden source assumption {token!r}")


def check_estimate_authority() -> None:
    """Зафиксировать target archive root и отсутствие чужой authority у estimate."""

    path = COMMANDS / "opsx-estimate.md"
    body = path.read_text(encoding="utf-8")
    required_claims = (
        "The only target archive root is",
        "<planningHome.changesDir>/archive",
        "VERIFY_AUTHORITY: NO",
        "ARCHIVE_VERDICT_WRITER: NO",
        "WRITE_BOUNDARY:",
        "CLOSES_SPECS: yes|no",
        "More candidate files exist than were examined",
        "both statuses are `unknown`; do not claim `PRIOR_EMPTY`",
    )
    for claim in required_claims:
        if claim not in body:
            raise AssertionError(f"{path}: missing estimate authority claim {claim!r}")

    command_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    activation = next(
        row for row in read_activation_rows() if row.get("command_id") == "opsx-estimate"
    )
    if activation.get("command_hash") != command_hash:
        raise AssertionError(f"{ACTIVATION}: opsx-estimate command_hash is stale")

    _ = require_installed_active_activation("opsx-estimate", command_hash=command_hash)
    verify_mandatory_transfer_batch_provenance(
        commands=["opsx-estimate"],
        command_hashes=[command_hash],
        label="opsx-estimate",
    )


def check_doctor_authority() -> None:
    """Зафиксировать read-only Doctor и честно отключённый destructive GO."""

    path = COMMANDS / "opsx-doctor.md"
    body = path.read_text(encoding="utf-8")
    required_claims = (
        "DELETE_AUTHORITY: NO",
        "ARCHIVE_AUTHORITY: NO",
        "`TARGETS_FOR_GO` is evidence preview only",
        "prior CHECK never authorizes deletion.",
        "Do not provide or execute a manual `Remove-Item`",
        "Every active path came from `openspec status",
    )
    for claim in required_claims:
        if claim not in body:
            raise AssertionError(f"{path}: missing Doctor authority claim {claim!r}")

    command_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    activation = next(
        row for row in read_activation_rows() if row.get("command_id") == "opsx-doctor"
    )
    if activation.get("command_hash") != command_hash:
        raise AssertionError(f"{ACTIVATION}: opsx-doctor command_hash is stale")

    verify_mandatory_transfer_batch_provenance(
        commands=["opsx-doctor"],
        command_hashes=[command_hash],
        label="opsx-doctor",
    )


def check_bug_workflow_authority() -> None:
    """Проверить, что bug-batch делегирует записи действующим target-владельцам."""

    required_by_command = {
        "opsx-bug-intake-clear": (
            "Do not create or edit `INDEX.md`, `LEDGER.md`",
            "Do not route a raw or clean row directly to `/opsx-fix-bug`",
            "`/opsx-materialize-bug-queue` remains the only command",
        ),
        "opsx-bug-family-plan": (
            "Do not create SOURCE-style `opsx/runs/bug-cases/family-plans/**`",
            "target `family/**` artifacts",
            "belong to `/opsx-bug-team`",
            "Grouping does not merge durable ownership.",
            "Mandatory input gate — FIRST ACTION",
            "STOP without tool calls",
        ),
        "opsx-bug-disposition": (
            "target has no accepted",
            "`dispositions/**` schema",
            "Never route `available` directly to fix-bug.",
            "CHANGE_CLEAR: NOT_EVALUATED",
            "VERIFY_STATUS: NOT_EVALUATED",
            "Mandatory argument gate — FIRST ACTION",
            "STOP without tool calls",
        ),
    }
    for command_id, claims in required_by_command.items():
        path = COMMANDS / f"{command_id}.md"
        body = path.read_text(encoding="utf-8")
        for claim in claims:
            if claim not in body:
                raise AssertionError(
                    f"{path}: missing target bug-workflow authority claim {claim!r}"
                )

    command_ids = [
        "opsx-bug-intake-clear",
        "opsx-bug-family-plan",
        "opsx-bug-disposition",
    ]
    command_hashes = [
        hashlib.sha256((COMMANDS / f"{command_id}.md").read_bytes()).hexdigest()
        for command_id in command_ids
    ]
    activation_rows = read_activation_rows()
    for command_id, command_hash in zip(command_ids, command_hashes, strict=True):
        activation = next(
            row for row in activation_rows if row.get("command_id") == command_id
        )
        if activation.get("command_hash") != command_hash:
            raise AssertionError(f"{ACTIVATION}: {command_id} command_hash is stale")

    verify_mandatory_transfer_batch_provenance(
        commands=command_ids,
        command_hashes=command_hashes,
        label="bug-workflow",
    )


def _command_id_from_surface_path(surface_path: str) -> str | None:
    """Извлечь command_id из пути поверхности `.cursor/commands/<id>.md`."""

    prefix = ".cursor/commands/"
    if not surface_path.startswith(prefix) or not surface_path.endswith(".md"):
        return None
    return Path(surface_path).stem


def load_full_surface_manifest() -> dict[str, object]:
    """Загрузить FULL_SURFACE_MANIFEST.json как объект верхнего уровня."""

    if not FULL_SURFACE_MANIFEST.is_file():
        raise AssertionError(f"missing full surface manifest: {FULL_SURFACE_MANIFEST}")
    payload = json.loads(FULL_SURFACE_MANIFEST.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError(f"{FULL_SURFACE_MANIFEST}: root must be an object")
    return cast(dict[str, object], payload)


def legacy_excluded_surface_rows(
    manifest: dict[str, object],
) -> dict[str, dict[str, object]]:
    """Сопоставить LEGACY_EXCLUDED command surfaces с их строками manifest."""

    rows = manifest.get("rows")
    if not isinstance(rows, list):
        raise AssertionError(f"{FULL_SURFACE_MANIFEST}: rows must be a list")
    by_command: dict[str, dict[str, object]] = {}
    for raw_row in rows:
        if not isinstance(raw_row, dict):
            continue
        row = cast(dict[str, object], raw_row)
        if row.get("disposition") != "LEGACY_EXCLUDED":
            continue
        command_id = _command_id_from_surface_path(str(row.get("path", "")))
        if command_id is None:
            continue
        by_command[command_id] = row
    return by_command


def check_legacy_excluded_commands() -> None:
    """Доказать LEGACY_EXCLUDED: не installed-active, не активируемые, manifest-согласованы."""

    manifest = load_full_surface_manifest()
    excluded_rows = legacy_excluded_surface_rows(manifest)
    if set(excluded_rows) != set(LEGACY_EXCLUDED_COMMAND_IDS):
        raise AssertionError(
            f"{FULL_SURFACE_MANIFEST}: LEGACY_EXCLUDED commands "
            f"expected {sorted(LEGACY_EXCLUDED_COMMAND_IDS)!r}, "
            f"got {sorted(excluded_rows)!r}"
        )

    terminal_counts = manifest.get("terminal_counts")
    if not isinstance(terminal_counts, dict):
        raise AssertionError(f"{FULL_SURFACE_MANIFEST}: terminal_counts missing")
    legacy_count = terminal_counts.get("LEGACY_EXCLUDED")
    rows = manifest.get("rows")
    if not isinstance(rows, list):
        raise AssertionError(f"{FULL_SURFACE_MANIFEST}: rows must be a list")
    row_legacy_total = sum(
        1
        for raw_row in rows
        if isinstance(raw_row, dict)
        and cast(dict[str, object], raw_row).get("disposition") == "LEGACY_EXCLUDED"
    )
    if legacy_count != row_legacy_total:
        raise AssertionError(
            f"{FULL_SURFACE_MANIFEST}: terminal_counts LEGACY_EXCLUDED={legacy_count!r} "
            f"but rows count {row_legacy_total}"
        )

    activation_ids = {str(row.get("command_id")) for row in read_activation_rows()}
    manifest_rows = parse_command_transfer_manifest()
    orientation = ORIENTATION.read_text(encoding="utf-8")
    router = META_ROUTER.read_text(encoding="utf-8")

    for command_id in LEGACY_EXCLUDED_COMMAND_IDS:
        row = excluded_rows[command_id]
        if row.get("target_present") is not False:
            raise AssertionError(
                f"{command_id}: LEGACY_EXCLUDED requires target_present=false"
            )
        if row.get("target_sha256") is not None:
            raise AssertionError(
                f"{command_id}: LEGACY_EXCLUDED must not carry target_sha256"
            )
        command_path = COMMANDS / f"{command_id}.md"
        if command_path.is_file():
            raise AssertionError(
                f"{command_id}: LEGACY_EXCLUDED command file must stay absent"
            )
        if command_id in activation_ids:
            raise AssertionError(
                f"{ACTIVATION}: {command_id} must not have an activation row"
            )
        slash = f"/{command_id}"
        if slash in orientation or slash in router:
            raise AssertionError(
                f"{command_id}: meta surfaces must not advertise {slash}"
            )
        manifest_state = manifest_rows.get(command_id)
        if manifest_state != "LEGACY_EXCLUDED":
            raise AssertionError(
                f"{TRANSFER_MANIFEST}: {command_id} must be LEGACY_EXCLUDED, "
                f"got {manifest_state!r}"
            )

    for command_id in ACTIVE_BUGBOT_ROUTES:
        if command_id not in TRANSFERRED_COMMANDS:
            raise AssertionError(
                f"{command_id}: must remain an installed-active transferred command"
            )
        active_path = COMMANDS / f"{command_id}.md"
        if not active_path.is_file():
            raise AssertionError(f"{command_id}: active bugbot route file missing")


def check_intent_align_optional_discovery_routing() -> None:
    """Evolution/explore expose optional intent-align without owning receipts."""

    evolution = COMMANDS / "opsx-evolution.md"
    explore = COMMANDS / "opsx-explore.md"
    evo_body = evolution.read_text(encoding="utf-8")
    exp_body = explore.read_text(encoding="utf-8")

    evolution_claims = (
        "Optional intent-align routing (chat-only, after `COPY_BLOCK`)",
        "/opsx-intent-align` can restate and hash-bind Owner product",
        "never treats alignment as mandatory",
        "default soft exit\n    remains Owner pastes into a new chat and chooses `/opsx-new`",
        "Do **not** put `/opsx-intent-align` or any `/opsx-*` inside",
        "evolution cannot\n    self-confirm `ALIGNED`",
        "DOWNSTREAM_RECEIPT_ENFORCEMENT: NOT_WIRED",
        "Owner Contract\n    Review remains downstream",
        "Never require `/opsx-intent-align` after `COPY_BLOCK`",
        "never auto-invoke it.",
        "Never write `opsx/intents/*`",
        "OPTIONAL (chat-only, never mandatory): /opsx-intent-align",
    )
    for claim in evolution_claims:
        if claim not in evo_body:
            raise AssertionError(
                f"{evolution}: missing optional intent-align routing claim {claim!r}"
            )

    explore_claims = (
        "Optional intent alignment (product semantics only)",
        "material product-semantic ambiguity",
        "not** technical design choices",
        "MAY recommend\n`/opsx-intent-align`",
        "Explore remains read-only: it **must\nnot** write intent receipts",
        "emit `INTENT_STATUS: ALIGNED`, or self-confirm",
        "Preserve `OWNER_INTENT` verbatim",
        "do\n**not** claim automatic receipt consumption (`NOT_WIRED`)",
        "Owner Contract Review\nstays downstream",
        "Don't write intent receipts**",
        "Don't self-confirm intent alignment**",
    )
    for claim in explore_claims:
        if claim not in exp_body:
            raise AssertionError(
                f"{explore}: missing optional intent-align routing claim {claim!r}"
            )

    fixture = (
        ROOT
        / ".cursor"
        / "commands"
        / "_fixtures"
        / "intent-align-optional-routing.md"
    )
    if not fixture.is_file():
        raise AssertionError(f"{fixture}: optional routing fixture missing")


def check_intent_alignment_authority() -> None:
    """Проверить самостоятельный gate и честно отсутствующих consumers."""

    path = COMMANDS / "opsx-intent-align.md"
    body = path.read_text(encoding="utf-8")
    required_claims = (
        "same turn as a newly generated, previously unseen restatement is invalid",
        "atomic no-overwrite publication",
        "Do not create a file in this state.",
        "does not claim that `/opsx-new` or",
        "`/opsx-ff` validates, transfers, invalidates, or consumes the receipt",
        "CONSUMER_VERIFICATION: NOT_WIRED",
        ".cursor/pipeline/intent-align/intent_alignment.py",
        ".cursor/pipeline/intent-align-tests/test_intent_alignment.py",
    )
    for claim in required_claims:
        if claim not in body:
            raise AssertionError(
                f"{path}: missing intent-alignment authority claim {claim!r}"
            )

    command_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    activation = next(
        row
        for row in read_activation_rows()
        if row.get("command_id") == "opsx-intent-align"
    )
    if activation.get("command_hash") != command_hash:
        raise AssertionError(
            f"{ACTIVATION}: opsx-intent-align command_hash is stale"
        )

    verify_mandatory_transfer_batch_provenance(
        commands=["opsx-intent-align"],
        command_hashes=[command_hash],
        label="opsx-intent-align",
    )


def check_risk_authority() -> None:
    """Проверить два risk-профиля, helper и честную частичную интеграцию."""

    path = COMMANDS / "opsx-risk.md"
    body = path.read_text(encoding="utf-8")
    required_claims = (
        "planning HIGH does not imply tasking HIGH",
        "planning LOW does not imply tasking LOW",
        "Absence of evidence is never `LOW`.",
        "Never infer not-required from missing files.",
        "Do not copy the planning profile.",
        "not yet automatically enforced",
        ".cursor/pipeline/risk/risk_receipt.py",
        ".cursor/pipeline/risk-tests/test_risk_receipt.py",
    )
    for claim in required_claims:
        if claim not in body:
            raise AssertionError(f"{path}: missing risk authority claim {claim!r}")

    helper = ROOT / ".cursor" / "pipeline" / "risk" / "risk_receipt.py"
    tests = (
        ROOT
        / ".cursor"
        / "pipeline"
        / "risk-tests"
        / "test_risk_receipt.py"
    )
    if not helper.is_file() or not tests.is_file():
        raise AssertionError("opsx-risk executable helper/tests are missing")

    command_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    activation = next(
        row for row in read_activation_rows() if row.get("command_id") == "opsx-risk"
    )
    if activation.get("command_hash") != command_hash:
        raise AssertionError(f"{ACTIVATION}: opsx-risk command_hash is stale")

    verify_mandatory_transfer_batch_provenance(
        commands=["opsx-risk"],
        command_hashes=[command_hash],
        label="opsx-risk",
        integration_status="PARTIAL",
    )


def check_ts_design_authority() -> None:
    """Проверить design-only writer и явно неактивированный ts-review."""

    path = COMMANDS / "opsx-ts-design.md"
    body = path.read_text(encoding="utf-8")
    required_claims = (
        "sole writer of",
        "Planning-risk is an independent scrutiny receipt",
        "does not unlock `design.md`",
        "TS_REVIEW_ACTIVATED: NO",
        "SUCCESSOR_STATUS: NOT_ACTIVATED",
        ".cursor/pipeline/technical-strategy/technical_strategy.py",
        ".cursor/pipeline/technical-strategy-tests/test_ts_design.py",
    )
    for claim in required_claims:
        if claim not in body:
            raise AssertionError(f"{path}: missing ts-design authority claim {claim!r}")

    helper = ROOT / ".cursor" / "pipeline" / "technical-strategy" / "technical_strategy.py"
    tests = (
        ROOT
        / ".cursor"
        / "pipeline"
        / "technical-strategy-tests"
        / "test_ts_design.py"
    )
    schema = (
        ROOT
        / ".cursor"
        / "pipeline"
        / "technical-strategy"
        / "ts_design.schema.json"
    )
    if not helper.is_file() or not tests.is_file() or not schema.is_file():
        raise AssertionError("opsx-ts-design helper, schema, or tests are missing")
    schema_body = schema.read_text(encoding="utf-8")
    if "input_revision_sha256" not in schema_body or "planning_risk_sha256" in schema_body:
        raise AssertionError(f"{schema}: schema is not the target design bind")

    command_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    activation = next(
        row
        for row in read_activation_rows()
        if row.get("command_id") == "opsx-ts-design"
    )
    if activation.get("command_hash") != command_hash:
        raise AssertionError(f"{ACTIVATION}: opsx-ts-design command_hash is stale")

    verify_mandatory_transfer_batch_provenance(
        commands=["opsx-ts-design"],
        command_hashes=[command_hash],
        label="opsx-ts-design",
        integration_status="PARTIAL",
    )


def check_ts_review_authority() -> None:
    """Проверить независимого review-писателя без unlock design.md."""

    path = COMMANDS / "opsx-ts-review.md"
    body = path.read_text(encoding="utf-8")
    required_claims = (
        "sole writer of",
        "ts/TS-REVIEW.md",
        "does not unlock `design.md`",
        "DESIGN_UNLOCK: NOT_WIRED",
        "INDEPENDENT_OF_TS_DESIGN_WRITER",
        "Zero blocking findings",
        "/opsx-new`, `/opsx-ff`, and `/opsx-continue` are unwired",
        "SOURCE_PARITY: PARTIAL",
    )
    for claim in required_claims:
        if claim not in body:
            raise AssertionError(f"{path}: missing ts-review authority claim {claim!r}")

    helper = ROOT / ".cursor" / "pipeline" / "technical-strategy" / "technical_strategy.py"
    tests = (
        ROOT
        / ".cursor"
        / "pipeline"
        / "technical-strategy-tests"
        / "test_ts_review.py"
    )
    schema = (
        ROOT
        / ".cursor"
        / "pipeline"
        / "technical-strategy"
        / "ts_review.schema.json"
    )
    if not helper.is_file() or not tests.is_file() or not schema.is_file():
        raise AssertionError("opsx-ts-review helper, schema, or tests are missing")
    helper_body = helper.read_text(encoding="utf-8")
    if "def publish_review" not in helper_body:
        raise AssertionError(f"{helper}: review publisher is missing")
    if "TS_REVIEW_NOT_ACTIVATED" in helper_body:
        raise AssertionError(f"{helper}: review action is still hard-disabled")
    if "INDEPENDENT_REVIEW_REQUIRED" not in helper_body:
        raise AssertionError(f"{helper}: independent review gate is missing")
    schema_body = schema.read_text(encoding="utf-8")
    if "design_unlock" not in schema_body or "NOT_WIRED" not in schema_body:
        raise AssertionError(f"{schema}: review schema does not keep design locked")

    command_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    activation = next(
        row
        for row in read_activation_rows()
        if row.get("command_id") == "opsx-ts-review"
    )
    if activation.get("command_hash") != command_hash:
        raise AssertionError(f"{ACTIVATION}: opsx-ts-review command_hash is stale")
    if activation.get("source_file_hash") != (
        "e7db75b216426313ed034a97f5d46051613a65c04f55938cc1ec0e18f18127b2"
    ):
        raise AssertionError(f"{ACTIVATION}: opsx-ts-review source hash drift")

    verify_mandatory_transfer_batch_provenance(
        commands=["opsx-ts-review"],
        command_hashes=[command_hash],
        label="opsx-ts-review",
        integration_status="PARTIAL",
        design_unlock_wired=False,
    )


def check_meta_update_all_snapshots_authority() -> None:
    """Проверить fan-out snapshot planning: inventory yes, APPLY fail closed."""

    path = COMMANDS / "opsx-meta-update-all-snapshots.md"
    body = path.read_text(encoding="utf-8")
    required_claims = (
        "lacks `--central`",
        "Never run `snapshot-apply` on TARGET from this command",
        "GO_SUPPORTED: false",
        "TOOL_CENTRAL_OVERRIDE: missing",
        "SNAPSHOT_CENTRAL_OVERRIDE_MISSING",
    )
    for claim in required_claims:
        if claim not in body:
            raise AssertionError(
                f"{path}: missing meta-update-all-snapshots claim {claim!r}"
            )
    if 'required_adapters: ["opsx-lite", "vue-npm"]' in body:
        raise AssertionError(f"{path}: must not require vue-npm adapter")

    command_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    activation = next(
        row
        for row in read_activation_rows()
        if row.get("command_id") == "opsx-meta-update-all-snapshots"
    )
    if activation.get("command_hash") != command_hash:
        raise AssertionError(
            f"{ACTIVATION}: opsx-meta-update-all-snapshots command_hash is stale"
        )
    if activation.get("source_file_hash") != (
        "fa5a3b4fd4491fda91c868f0e2c04cbe4720ffc7b62a1562fd247ab7b2adeda3"
    ):
        raise AssertionError(
            f"{ACTIVATION}: opsx-meta-update-all-snapshots source hash drift"
        )

    verify_mandatory_transfer_batch_provenance(
        commands=["opsx-meta-update-all-snapshots"],
        command_hashes=[command_hash],
        label="opsx-meta-update-all-snapshots",
        integration_status="PARTIAL",
    )


def check_opsx_models_authority() -> None:
    """Check active Temporal model-routing authority after Cursor SDK activation."""

    path = COMMANDS / "opsx-models.md"
    body = path.read_text(encoding="utf-8")
    required_claims = (
        "IMPLEMENTATION_STATUS: IMPLEMENTED",
        "RUNTIME_STATUS: ACTIVE",
        "SOURCE_PARITY: ADAPTED",
        "GO_SUPPORTED: true",
        "READ_ONLY: true (default)",
        "TEMPORAL_EFFECTIVE_ROUTING: AVAILABLE",
        "TRANSPORT: CURSOR_PYTHON_SDK",
        "tools/temporal/model_config.py",
        "tools/temporal/agent-models.json",
        "scope=effective|project|runtime",
        "EXECUTION_MODEL_AUTHORITY:",
        "MODEL_CONFIG_APPLIED",
    )
    for claim in required_claims:
        if claim not in body:
            raise AssertionError(f"{path}: missing opsx-models claim {claim!r}")
    if 'required_adapters: ["opsx-lite", "vue-npm"]' in body:
        raise AssertionError(f"{path}: must not require vue-npm adapter")

    command_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    activation = next(
        row for row in read_activation_rows() if row.get("command_id") == "opsx-models"
    )
    _ = require_installed_active_activation("opsx-models", command_hash=command_hash)
    if activation.get("source_file_hash") != (
        "2b0012420a882cd65561ad104797d92f3dc7f5ccd84522190212c95869fbcbf6"
    ):
        raise AssertionError(f"{ACTIVATION}: opsx-models source hash drift")

    provenance_rows = [
        json.loads(raw_line)
        for raw_line in PROVENANCE.read_text(encoding="utf-8").splitlines()
        if raw_line.strip()
    ]
    historical = [
        row
        for row in provenance_rows
        if row.get("event") == "full_command_transfer_batch"
        and row.get("commands") == ["opsx-models"]
    ]
    if historical:
        if len(historical) != 1:
            raise AssertionError(
                f"{PROVENANCE}: expected one historical opsx-models transfer event, "
                f"got {len(historical)}"
            )
        if historical[0].get("source_commit") != _TRANSFER_BATCH_SOURCE_COMMIT:
            raise AssertionError(
                f"{PROVENANCE}: historical opsx-models source commit drift"
            )

    activations = [
        row
        for row in provenance_rows
        if row.get("event") == "opsx_models_sdk_activation"
        and row.get("commands") == ["opsx-models"]
    ]
    if len(activations) != 1:
        raise AssertionError(
            f"{PROVENANCE}: expected one opsx-models SDK activation event, "
            f"got {len(activations)}"
        )
    event = activations[0]
    if event.get("command_hashes") != [command_hash]:
        raise AssertionError(f"{PROVENANCE}: opsx-models SDK activation hash is stale")
    if event.get("integration_status") != "FULL":
        raise AssertionError(f"{PROVENANCE}: opsx-models SDK activation must be FULL")
    if event.get("transport") != "cursor-python-sdk":
        raise AssertionError(f"{PROVENANCE}: opsx-models transport must be cursor-python-sdk")
    required_runtime = {
        "tools/temporal/model_config.py",
        "tools/temporal/agent-models.json",
        "tools/temporal/runtime.py",
        "tools/temporal/active_model_route_coverage.py",
        "tools/cursor_executor/pyproject.toml",
        "tools/cursor_executor/requirements.txt",
    }
    runtime_hashes = event.get("runtime_hashes") or {}
    if set(runtime_hashes) != required_runtime:
        raise AssertionError(f"{PROVENANCE}: opsx-models runtime identity set mismatch")


def check_meta_add_command_authority() -> None:
    """Проверить bounded intake router и блок автономной SOURCE-доставки."""

    path = COMMANDS / "opsx-meta-add-command.md"
    body = path.read_text(encoding="utf-8")
    required_claims = (
        "Do **not** claim `OPSX_COMMAND_DELIVERY_GREEN`",
        "P11_COMMIT: BLOCKED",
        "GRAPH_WRITER: NO",
        "git cat-file -e HEAD:",
        "RESUME_UNCOMMITTED",
        "/opsx-meta-import-command` only",
    )
    for claim in required_claims:
        if claim not in body:
            raise AssertionError(
                f"{path}: missing meta-add-command authority claim {claim!r}"
            )

    command_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    activation = next(
        row
        for row in read_activation_rows()
        if row.get("command_id") == "opsx-meta-add-command"
    )
    if activation.get("command_hash") != command_hash:
        raise AssertionError(
            f"{ACTIVATION}: opsx-meta-add-command command_hash is stale"
        )
    if activation.get("source_file_hash") != (
        "8d21e85dbfe99d793ce7bcc3a760d55fc6091e0f190af4ed9a35971292d3509e"
    ):
        raise AssertionError(
            f"{ACTIVATION}: opsx-meta-add-command source hash drift"
        )

    verify_mandatory_transfer_batch_provenance(
        commands=["opsx-meta-add-command"],
        command_hashes=[command_hash],
        label="opsx-meta-add-command transfer",
        integration_status="PARTIAL",
    )


def check_run_build_authority() -> None:
    """Проверить manual poetry_package helper и отказ bounded без S6 capability."""

    path = COMMANDS / "opsx-run-build.md"
    body = path.read_text(encoding="utf-8")
    required_claims = (
        "DURABLE_RECEIPT: NONE",
        "bounded-verify/results/**` | **NONE**",
        "S6_PACKAGE_BUILD_CAPABILITY_UNREGISTERED",
        "EXECUTOR_ADAPTER_GAP",
        "OUTPUT_DIR_REQUIRED",
        "RUN_RESULT: chat-only",
        ".cursor/pipeline/run-build/run_build.py",
        ".cursor/pipeline/run-build-tests/test_run_build_manual.py",
        "poetry.core.masonry.api",
    )
    for claim in required_claims:
        if claim not in body:
            raise AssertionError(
                f"{path}: missing run-build authority claim {claim!r}"
            )

    command_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    activation = next(
        row
        for row in read_activation_rows()
        if row.get("command_id") == "opsx-run-build"
    )
    if activation.get("command_hash") != command_hash:
        raise AssertionError(f"{ACTIVATION}: opsx-run-build command_hash is stale")
    if activation.get("source_file_hash") != (
        "ad5b436b69ff8398ee59f68e35a3cc4d204291dc1c895e318495a7bafa33d102"
    ):
        raise AssertionError(f"{ACTIVATION}: opsx-run-build source hash drift")

    verify_mandatory_transfer_batch_provenance(
        commands=["opsx-run-build"],
        command_hashes=[command_hash],
        label="opsx-run-build",
        integration_status="PARTIAL",
    )


def check_run_tests_authority() -> None:
    """Проверить manual pytest helper и отказ bounded без S6 executor."""

    path = COMMANDS / "opsx-run-tests.md"
    body = path.read_text(encoding="utf-8")
    required_claims = (
        "DURABLE_RECEIPT: NONE",
        "bounded-verify/results/**` | **NONE**",
        "S6_TEST_CAPABILITY_UNREGISTERED",
        "DESTRUCTIVE_LOCAL_BLOCKED",
        "RUN_RESULT: chat-only",
        ".cursor/pipeline/run-tests/run_tests.py",
        ".cursor/pipeline/run-tests-tests/test_run_tests_manual.py",
    )
    for claim in required_claims:
        if claim not in body:
            raise AssertionError(
                f"{path}: missing run-tests authority claim {claim!r}"
            )

    command_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    activation = next(
        row
        for row in read_activation_rows()
        if row.get("command_id") == "opsx-run-tests"
    )
    if activation.get("command_hash") != command_hash:
        raise AssertionError(f"{ACTIVATION}: opsx-run-tests command_hash is stale")
    if activation.get("source_file_hash") != (
        "0dc8aeb0619f9ebf57ab2c9d58df3aae81752d38136819012c142cd55eff7438"
    ):
        raise AssertionError(f"{ACTIVATION}: opsx-run-tests source hash drift")

    verify_mandatory_transfer_batch_provenance(
        commands=["opsx-run-tests"],
        command_hashes=[command_hash],
        label="opsx-run-tests",
    )


def check_run_typecheck_authority() -> None:
    """Проверить typecheck-facade: sole executor delegate, без второго writer."""

    path = COMMANDS / "opsx-run-typecheck.md"
    body = path.read_text(encoding="utf-8")
    required_claims = (
        "does not publish `results/*.json` itself",
        "Do **not** call `bounded_verify_executor` with a different `check_id`",
        "Manual SOURCE diagnostic path `typecheck_step=<frozen-step>` is **not**",
        "RESULT_WRITER: NO",
        "LEDGER_WRITER: NO",
    )
    for claim in required_claims:
        if claim not in body:
            raise AssertionError(
                f"{path}: missing run-typecheck authority claim {claim!r}"
            )

    command_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    activation = next(
        row
        for row in read_activation_rows()
        if row.get("command_id") == "opsx-run-typecheck"
    )
    if activation.get("command_hash") != command_hash:
        raise AssertionError(
            f"{ACTIVATION}: opsx-run-typecheck command_hash is stale"
        )
    if activation.get("source_file_hash") != (
        "c2049efd5812c4e3a93db1a577d76e89d2fc719eb9ba9480c845666c11f9d134"
    ):
        raise AssertionError(f"{ACTIVATION}: opsx-run-typecheck source hash drift")

    verify_mandatory_transfer_batch_provenance(
        commands=["opsx-run-typecheck"],
        command_hashes=[command_hash],
        label="opsx-run-typecheck transfer",
        integration_status="PARTIAL",
    )


def check_security_triage_authority() -> None:
    """Проверить partial security-triage: локальные цитаты, без чужого adapter."""

    path = COMMANDS / "opsx-security-triage.md"
    body = path.read_text(encoding="utf-8")
    required_claims = (
        "SECURITY_TRIAGE_DECLARED: NO",
        "CAPABILITY_PARITY: PARTIAL",
        "CONFIDENCE_IS_PROOF: NO",
        "ABSENCE_IS_NOT_A_DISPOSITION",
        "plane_misroute",
        "path_outside_workspace",
        "BUG_LANE_OWNER: NO",
        "SECURITY_REVIEW_VERDICT_AUTHORITY: NO",
        ".cursor/pipeline/security-triage/security_triage.py",
    )
    for claim in required_claims:
        if claim not in body:
            raise AssertionError(
                f"{path}: missing security-triage authority claim {claim!r}"
            )
    if 'required_adapters: ["opsx-lite", "vue-npm"]' in body:
        raise AssertionError(f"{path}: invented vue-npm adapter requirement")

    command_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    activation = next(
        row
        for row in read_activation_rows()
        if row.get("command_id") == "opsx-security-triage"
    )
    if activation.get("command_hash") != command_hash:
        raise AssertionError(
            f"{ACTIVATION}: opsx-security-triage command_hash is stale"
        )
    if activation.get("source_file_hash") != (
        "ae11f035d4eca3fef640226fe24d247e88f3f4569e362cdc43b10a6efa99b399"
    ):
        raise AssertionError(
            f"{ACTIVATION}: opsx-security-triage source hash drift"
        )

    verify_mandatory_transfer_batch_provenance(
        commands=["opsx-security-triage"],
        command_hashes=[command_hash],
        label="opsx-security-triage transfer",
        integration_status="PARTIAL",
    )


def check_activation() -> None:
    """Проверить уникальную ACTIVE-регистрацию перенесённых команд."""

    rows = read_activation_rows()
    for command_id in TRANSFERRED_COMMANDS:
        matching = [row for row in rows if row.get("command_id") == command_id]
        if len(matching) != 1:
            raise AssertionError(
                f"{command_id}: expected one activation row, got {len(matching)}"
            )
        row = matching[0]
        if row.get("runtime_class") not in INSTALLED_ACTIVE_RUNTIME_CLASSES or row.get(
            "installed"
        ) is not True:
            raise AssertionError(f"{command_id}: activation is not installed ACTIVE")
        target_path = ROOT / str(row.get("target_path", ""))
        if not target_path.is_file():
            raise AssertionError(f"{command_id}: activation target does not exist")

    for command_id in LEGACY_EXCLUDED_COMMAND_IDS:
        matching = [row for row in rows if row.get("command_id") == command_id]
        if matching:
            raise AssertionError(
                f"{command_id}: LEGACY_EXCLUDED must not appear in activation registry"
            )


def check_router_wiring() -> None:
    """Проверить, что meta router и shared orientation знают новые поверхности."""

    orientation = ORIENTATION.read_text(encoding="utf-8")
    router = META_ROUTER.read_text(encoding="utf-8")
    for command_id in META_ROUTED_COMMANDS:
        slash_name = f"/{command_id}"
        if slash_name not in orientation:
            raise AssertionError(f"{ORIENTATION}: missing {slash_name}")
        if slash_name not in router:
            raise AssertionError(f"{META_ROUTER}: missing {slash_name}")
    if "## Create vs Fix (authoritative)" not in orientation:
        raise AssertionError(f"{ORIENTATION}: missing create-versus-fix authority")


def check_transfer_manifest() -> None:
    """Проверить полноту и допустимые состояния 100-командного ledger."""

    raw_rows = parse_command_transfer_manifest_rows()
    if len(raw_rows) != 112:
        raise AssertionError(
            f"{TRANSFER_MANIFEST}: expected 112 command rows, got {len(raw_rows)}"
        )
    command_ids = [command_id for command_id, _ in raw_rows]
    if len(set(command_ids)) != len(command_ids):
        raise AssertionError(f"{TRANSFER_MANIFEST}: duplicate command rows")
    manifest_rows = dict(raw_rows)
    invalid_states = sorted(
        {state for state in manifest_rows.values() if state not in ALLOWED_TRANSFER_STATES}
    )
    if invalid_states:
        raise AssertionError(
            f"{TRANSFER_MANIFEST}: invalid states {invalid_states!r}"
        )
    for command_id in TRANSFERRED_COMMANDS:
        state = manifest_rows[command_id]
        if state not in INSTALLED_ACTIVE_MANIFEST_STATES:
            raise AssertionError(
                f"{TRANSFER_MANIFEST}: {command_id} has unexpected state {state}"
            )
    for command_id in LEGACY_EXCLUDED_COMMAND_IDS:
        state = manifest_rows[command_id]
        if state != "LEGACY_EXCLUDED":
            raise AssertionError(
                f"{TRANSFER_MANIFEST}: {command_id} must be LEGACY_EXCLUDED, "
                f"got {state!r}"
            )


def check_terminal_transfer_evidence() -> None:
    """Validate proof-backed terminal states against current command bytes."""

    active_path = ROOT / "opsx" / "registry" / "active_command_activation_proof.json"
    existing_path = (
        ROOT / "opsx" / "registry" / "existing_adaptation_reconciliation.json"
    )
    acts = {x["command_id"]: x for x in read_activation_rows()}
    manifest_rows = parse_command_transfer_manifest()

    if not active_path.is_file():
        raise AssertionError(
            f"{active_path}: missing FUNCTIONAL_PROVEN activation proof registry"
        )
    active = json.loads(active_path.read_text(encoding="utf-8"))
    ar = {
        str(x["command"]): x
        for x in cast(list[dict[str, object]], active.get("commands", []))
    }

    existing: dict[str, object] = {}
    er: dict[str, object] = {}
    needs_reconciliation = any(
        state in {"TARGET_NATIVE_RETAINED", "TARGET_NATIVE_RECONCILED"}
        for state in manifest_rows.values()
    )
    if needs_reconciliation:
        if not existing_path.is_file():
            raise AssertionError(
                f"{existing_path}: missing adaptation reconciliation evidence"
            )
        existing = json.loads(existing_path.read_text(encoding="utf-8"))
        er = {
            str(x["command"]): x
            for x in cast(list[dict[str, object]], existing.get("commands", []))
        }

    for cid, state in manifest_rows.items():
        path = COMMANDS / f"{cid}.md"
        if state == "LEGACY_EXCLUDED":
            if cid not in LEGACY_EXCLUDED_COMMAND_IDS:
                raise AssertionError(f"{cid}: unexpected LEGACY_EXCLUDED ledger row")
            if cid in acts:
                raise AssertionError(f"{cid}: LEGACY_EXCLUDED must not be activated")
            if path.is_file():
                raise AssertionError(f"{cid}: LEGACY_EXCLUDED file must be absent")
            surface_rows = legacy_excluded_surface_rows(load_full_surface_manifest())
            surface = surface_rows.get(cid)
            if not surface or surface.get("target_present") is not False:
                raise AssertionError(f"{cid}: full surface LEGACY_EXCLUDED proof missing")
            continue
        if state in INSTALLED_ACTIVE_MANIFEST_STATES:
            if not path.is_file():
                raise AssertionError(
                    f"{cid}: installed-active ledger command file missing"
                )
            command_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            if cid not in acts:
                raise AssertionError(f"{cid}: installed-active activation row missing")
            if (
                cid not in DEFERRED_ACTIVATION_HASH_DRIFT_COMMANDS
                and acts[cid].get("command_hash") != command_hash
            ):
                raise AssertionError(f"{cid}: installed-active activation hash stale")
            if state == "FUNCTIONAL_PROVEN":
                row = ar.get(cid)
                if (
                    not row
                    or row.get("proof_pass") is not True
                    or row.get("command_sha256") != command_hash
                ):
                    raise AssertionError(f"{cid}: FUNCTIONAL_PROVEN proof stale")
            continue
        if not path.is_file():
            raise AssertionError(f"{cid}: missing command file for state {state!r}")
        command_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if state == "TARGET_NATIVE_RETAINED":
            row = er.get(cid)
            if (
                not row
                or row.get("classification") != state
                or row.get("baseline_hash") != command_hash
                or row.get("current_hash") != command_hash
            ):
                raise AssertionError(f"{cid}: retained evidence stale")
        elif state == "TARGET_NATIVE_RECONCILED":
            row = er.get(cid)
            if (
                not row
                or row.get("classification") != state
                or row.get("current_hash") != command_hash
            ):
                raise AssertionError(f"{cid}: reconciled evidence stale")
            summary = existing.get("summary", {})
            if not isinstance(summary, dict) or summary.get("gates_passed") is not True:
                raise AssertionError(f"{cid}: reconciliation gates not proven")


def _git_blob_sha256(relative_path: str, revision: str = "HEAD") -> str:
    """Hash canonical committed Git blob bytes, independent of checkout line endings."""
    blob = subprocess.check_output(
        ["git", "-C", str(ROOT), "show", f"{revision}:{relative_path}"]
    )
    return hashlib.sha256(blob).hexdigest()


def run_mandatory_transfer_acceptance_checks() -> None:
    """Run static transfer acceptance obligations (raises ``AssertionError`` on failure)."""

    for command_id, contract in TRANSFERRED_COMMANDS.items():
        check_command(command_id, contract)
    check_estimate_authority()
    check_doctor_authority()
    check_bug_workflow_authority()
    check_legacy_excluded_commands()
    check_intent_align_optional_discovery_routing()
    check_intent_alignment_authority()
    check_risk_authority()
    check_ts_design_authority()
    check_ts_review_authority()
    check_run_typecheck_authority()
    check_run_build_authority()
    check_run_tests_authority()
    check_security_triage_authority()
    check_meta_add_command_authority()
    check_meta_update_all_snapshots_authority()
    check_opsx_models_authority()
    check_activation()
    check_router_wiring()
    check_transfer_manifest()
    check_terminal_transfer_evidence()


def check_stroy_target_terminal_transfer() -> dict[str, Any]:
    """Closure must PASS before transfer acceptance; receipts are not terminal proof."""

    closure = evaluate_dependency_closure(ROOT)
    if closure.get("status") != "PASS":
        closure["terminal_authority"] = "dependency_closure_gate"
        return closure
    try:
        run_mandatory_transfer_acceptance_checks()
    except AssertionError as error:
        failure = dict(closure)
        failure["status"] = "FAIL"
        failure["terminal_authority"] = "transfer_acceptance_obligations"
        failure["transfer_acceptance_error"] = str(error)
        return failure
    closure["terminal_authority"] = "dependency_closure_and_transfer_acceptance"
    closure["transfer_acceptance"] = "PASS"
    return closure


def main() -> int:
    """Запустить пакет проверок и вернуть код процесса."""

    target_state = ROOT / "opsx" / "transfer" / "TRANSFER_STATE.json"
    if target_state.is_file():
        import json

        result = check_stroy_target_terminal_transfer()
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("status") == "PASS" else 1

    run_mandatory_transfer_acceptance_checks()
    print(
        "PASS command_transfer_contracts "
        f"commands={len(TRANSFERRED_COMMANDS)} activation=unique "
        "wiring=present manifest=112"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
