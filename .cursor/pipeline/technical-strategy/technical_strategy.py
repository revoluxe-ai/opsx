"""Публикация Technical Strategy и независимого TS-REVIEW.

``design`` пишет только ``<changeRoot>/ts/TS-DESIGN.*`` и связывает их с
текущими байтами owner-contract, proposal и specs. ``review`` пишет только
``ts/TS-REVIEW.*`` и требует отдельно переданный ``reviewer_id`` плюс файл
независимых findings. ``design.md`` и planning-risk не являются выходом.
PASS не открывает ``design.md``: потребители ``/opsx-new``, ``/opsx-ff`` и
``/opsx-continue`` этот receipt не читают.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, NoReturn, cast


CONTRACT_ID = "OPSX-WB-TS-DESIGN-01"
REVIEW_CONTRACT_ID = "OPSX-WB-TS-REVIEW-01"
SCHEMA_PATH = Path(__file__).with_name("ts_design.schema.json")
REVIEW_SCHEMA_PATH = Path(__file__).with_name("ts_review.schema.json")
REVIEW_SCHEMA_VERSION = "target-technical-strategy-review-v1"
INDEPENDENCE_TOKEN = "INDEPENDENT_OF_TS_DESIGN_WRITER"
FORBIDDEN_REVIEWER_IDS = {
    "opsx-ts-design",
    "design-writer",
    "ts-design-writer",
    "self",
    "author_agent",
}
FINDING_SEVERITIES = {"BLOCKING", "NON_BLOCKING"}
UNTRUSTED_EVIDENCE_KEYS = {
    "review_state",
    "verified_state",
    "design_unlock",
    "design_md_write",
}
CHANGE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SCHEMA_VERSION = "target-technical-strategy-v1"
STRATEGY_CLASSES = {
    "NEW_ARCHITECTURE",
    "EXTEND_EXISTING_ARCHITECTURE",
    "EXISTING_ARCHITECTURE_REUSED",
    "NO_MATERIAL_ARCHITECTURAL_CHANGE",
}
TS_STATUSES = {"DRAFT", "READY_FOR_REVIEW", "BLOCKED"}
BLOCK_REASONS = {
    "UPSTREAM_SEMANTIC_REALIGNMENT_REQUIRED",
    "EVIDENCE_GAP",
}
REVIEW_STATES = {"PASS", "DEFECTS_FOUND", "BLOCKED"}
FINDING_TYPES = {
    "ARCHITECTURE_DEFECT",
    "OWNER_SEMANTIC_GAP",
    "EVIDENCE_GAP",
    "SPEC_DRIFT",
    "SCHEMA_VIOLATION",
}
FINDING_FIELDS = (
    "id",
    "type",
    "severity",
    "violated_authority",
    "concrete_problem",
    "consequence",
    "evidence",
    "required_correction_class",
    "target_artifact",
)
NARRATIVE_FIELDS = (
    "problem_model",
    "owner_outcome_to_preserve",
    "existing_architecture_context",
    "state_ownership",
    "data_flow",
    "control_flow",
    "concurrency_model",
    "failure_model",
    "consistency_model",
    "persistence_model",
    "security_boundaries",
    "observability_requirements",
    "testability_implications",
)
ASSESSMENT_NAMES = (
    "migration_assessment",
    "backward_compatibility_assessment",
    "rollout_assessment",
    "rollback_assessment",
)
CORE_DECISIONS = ("sot_placement", "durability", "write_authority", "recovery")
MIGRATION_REQUIRED_KEYS = (
    "persisted_representation_change",
    "existing_data_impact",
    "migration_direction",
    "deployment_ordering",
    "compatibility_window",
    "rollback_implications",
    "verification_obligations",
)
FORBIDDEN_PAYLOAD_KEYS = {
    "planning_risk_sha256",
    "review_state",
    "verified_state",
    "design_unlock",
    "design_md_write",
}
STRIP_KEYS = {"created_at", "updated_at", "author_agent"}
PLACEHOLDERS = {"UNKNOWN", "DEFINED"}


class TechnicalStrategyError(ValueError):
    """Вход, граница записи или freshness не позволяют публиковать артефакт."""


def _fail(reason: str) -> NoReturn:
    raise TechnicalStrategyError(reason)


def sha256_text(value: str) -> str:
    """Вернуть SHA256 hex для UTF-8 текста."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalized_text(path: Path) -> str:
    raw = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return raw.decode("utf-8")


def _file_sha256(path: Path) -> str:
    return sha256_text(_normalized_text(path))


def sort_keys_deep(value: Any) -> Any:
    """Рекурсивно отсортировать ключи и убрать volatile поля из hash input."""

    if isinstance(value, list):
        return [sort_keys_deep(item) for item in cast(list[object], value)]
    if isinstance(value, dict):
        source = cast(dict[str, Any], value)
        return {
            key: sort_keys_deep(source[key])
            for key in sorted(source)
            if key not in STRIP_KEYS
        }
    return value


def ts_design_sha256(document: dict[str, Any]) -> str:
    """Посчитать TS_DESIGN_SHA256 по каноническому JSON без volatile полей.

    Args:
        document: Разобранный ``TS-DESIGN.json``.

    Returns:
        Нижний регистр SHA256.
    """

    payload = json.dumps(
        sort_keys_deep(document),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256_text(payload)


def _require_dir(path: Path, reason: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_dir():
        _fail(reason)
    return resolved


def _require_file(path: Path, reason: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_file():
        _fail(reason)
    return resolved


def _within(root: Path, candidate: Path, reason: str) -> Path:
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        _fail(reason)
    return resolved


def _load_schema() -> dict[str, Any]:
    """Прочитать локальную schema и отказаться от пустого stub."""

    try:
        raw = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TechnicalStrategyError("SCHEMA_INVALID") from error
    if not isinstance(raw, dict):
        _fail("SCHEMA_INVALID")
    return cast(dict[str, Any], raw)


def _assert_schema_shape(document: dict[str, Any]) -> None:
    """Проверить, что документ содержит required-ключи рабочей schema.

    Полный JSON Schema validator не подключается. Required-список schema
    обязан совпадать с публикуемым TS-DESIGN, включая target provenance
    без ``planning_risk_sha256``.
    """

    schema = _load_schema()
    required = schema.get("required")
    properties = schema.get("properties")
    if (
        not isinstance(required, list)
        or not required
        or not isinstance(properties, dict)
    ):
        _fail("SCHEMA_INVALID")
    for key in cast(list[object], required):
        if not isinstance(key, str) or key not in document:
            _fail(f"SCHEMA_REQUIRED:{key}")
    provenance_schema = cast(dict[str, Any], properties).get("upstream_provenance")
    if not isinstance(provenance_schema, dict):
        _fail("SCHEMA_INVALID")
    provenance_required = provenance_schema.get("required")
    if not isinstance(provenance_required, list) or not provenance_required:
        _fail("SCHEMA_INVALID")
    stored = document.get("upstream_provenance")
    if not isinstance(stored, dict):
        _fail("SCHEMA_REQUIRED:upstream_provenance")
    stored_map = cast(dict[str, Any], stored)
    for key in cast(list[object], provenance_required):
        if not isinstance(key, str) or key not in stored_map:
            _fail(f"SCHEMA_REQUIRED:upstream_provenance.{key}")
    if "planning_risk_sha256" in stored_map:
        _fail("FORBIDDEN_KEY:planning_risk_sha256")


def _reject_stale_claim(payload: dict[str, Any], provenance: dict[str, str]) -> None:
    """Отклонить payload, который заявляет несовпадающий upstream bind.

    Отсутствие ``upstream_provenance`` означает «привязать к live bytes».
    Любой заявленный ключ обязан совпасть с только что посчитанным bind.
    """

    claimed = payload.get("upstream_provenance")
    if claimed is None:
        return
    if not isinstance(claimed, dict):
        _fail("INPUT_REVISION_STALE")
    for key, value in cast(dict[str, Any], claimed).items():
        if key not in provenance or provenance[key] != value:
            _fail("INPUT_REVISION_STALE")


def _reject_forbidden_keys(value: object, trail: str = "") -> None:
    if isinstance(value, dict):
        source = cast(dict[str, object], value)
        for key, child in source.items():
            if key in FORBIDDEN_PAYLOAD_KEYS:
                _fail(f"FORBIDDEN_KEY:{key}")
            _reject_forbidden_keys(child, f"{trail}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(cast(list[object], value)):
            _reject_forbidden_keys(child, f"{trail}[{index}]")
    elif isinstance(value, str) and value.strip() in PLACEHOLDERS:
        _fail(f"PLACEHOLDER_FORBIDDEN:{trail or 'value'}")


def _load_json_object(path: Path, reason: str) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TechnicalStrategyError(reason) from error
    if not isinstance(raw, dict):
        _fail(reason)
    return cast(dict[str, Any], raw)


def _sha256_hex(value: object) -> bool:
    """Вернуть True для нижнего регистра SHA256 из 64 hex-символов."""

    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(char in "0123456789abcdef" for char in value)


def _validate_findings(raw: object) -> list[dict[str, Any]]:
    """Проверить список findings и отбросить запрещённые типы.

    Args:
        raw: JSON-массив из независимого evidence-файла.

    Returns:
        Копии findings только с каноническими полями.

    Raises:
        TechnicalStrategyError: Если поле, тип, severity или target запрещены.
    """

    if not isinstance(raw, list):
        _fail("FINDINGS_INVALID")
    findings: list[dict[str, Any]] = []
    for item in cast(list[object], raw):
        if not isinstance(item, dict):
            _fail("FINDINGS_INVALID")
        finding = cast(dict[str, Any], item)
        for field in FINDING_FIELDS:
            value = finding.get(field)
            if not isinstance(value, str) or not value.strip():
                _fail(f"FINDING_FIELD_REQUIRED:{field}")
        finding_type = cast(str, finding["type"])
        if finding_type == "OWNER_DECISION":
            _fail("OWNER_DECISION_FORBIDDEN")
        if finding_type not in FINDING_TYPES:
            _fail("FINDING_TYPE_INVALID")
        if finding.get("severity") not in FINDING_SEVERITIES:
            _fail("FINDING_SEVERITY_INVALID")
        target = cast(str, finding["target_artifact"])
        if finding_type == "ARCHITECTURE_DEFECT" and target != "ts/TS-DESIGN.json":
            _fail("ARCHITECTURE_DEFECT_TARGETS_DESIGN_MD")
        if target == "design.md":
            _fail("DESIGN_MD_TARGET_FORBIDDEN")
        findings.append({field: finding[field] for field in FINDING_FIELDS})
    return findings


def _load_review_evidence(
    path: Path, reviewer_id: str
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Прочитать независимое evidence и отказаться от самовыданного PASS.

    Args:
        path: JSON-файл, который подготовил ревьюер, а не design writer.
        reviewer_id: Явный идентификатор с CLI, обязан совпасть с файлом.

    Returns:
        Пара из bind-полей evidence и проверенных findings.

    Raises:
        TechnicalStrategyError: Если независимое ревью не доказано.
    """

    normalized = reviewer_id.strip()
    if not normalized:
        _fail("INDEPENDENT_REVIEW_REQUIRED")
    if normalized.casefold() in FORBIDDEN_REVIEWER_IDS:
        _fail("REVIEWER_NOT_INDEPENDENT")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TechnicalStrategyError("INDEPENDENT_REVIEW_REQUIRED") from error
    if not isinstance(raw, dict):
        _fail("INDEPENDENT_REVIEW_REQUIRED")
    evidence = cast(dict[str, Any], raw)
    for key in UNTRUSTED_EVIDENCE_KEYS:
        if key in evidence:
            _fail("UNTRUSTED_PASS_CLAIM")
    if evidence.get("reviewer_id") != normalized:
        _fail("REVIEWER_ID_MISMATCH")
    if evidence.get("independence") != INDEPENDENCE_TOKEN:
        _fail("INDEPENDENT_REVIEW_REQUIRED")
    design_hash = evidence.get("bound_ts_design_sha256")
    input_hash = evidence.get("bound_input_revision_sha256")
    if not _sha256_hex(design_hash) or not _sha256_hex(input_hash):
        _fail("INDEPENDENT_REVIEW_REQUIRED")
    findings = _validate_findings(evidence.get("findings"))
    return (
        {
            "reviewer_id": normalized,
            "bound_ts_design_sha256": cast(str, design_hash),
            "bound_input_revision_sha256": cast(str, input_hash),
        },
        findings,
    )


def _string_list(value: object, key: str) -> list[str]:
    if not isinstance(value, list):
        _fail(f"FIELD_REQUIRED:{key}")
    items = cast(list[object], value)
    if any(not isinstance(item, str) or not item.strip() for item in items):
        _fail(f"FIELD_REQUIRED:{key}")
    return cast(list[str], items)


def _text(source: dict[str, Any], key: str) -> str:
    value = source.get(key)
    if not isinstance(value, str) or not value.strip():
        _fail(f"FIELD_REQUIRED:{key}")
    return value


def _core_decision(value: object, key: str, *, ready: bool) -> dict[str, str]:
    if not isinstance(value, dict):
        _fail(f"CORE_DECISION_INVALID:{key}")
    decision = cast(dict[str, Any], value)
    status = decision.get("status")
    if status not in {"SPECIFIED", "NOT_APPLICABLE"}:
        _fail(f"CORE_DECISION_INVALID:{key}")
    if not ready:
        return {"status": cast(str, status)}
    if status == "NOT_APPLICABLE":
        waiver = decision.get("waiver_evidence")
        if not isinstance(waiver, str) or len(waiver.strip()) < 10:
            _fail(f"WAIVER_REQUIRED:{key}")
        return {"status": "NOT_APPLICABLE", "waiver_evidence": waiver}
    detail = decision.get("detail")
    if not isinstance(detail, str) or not detail.strip():
        _fail(f"CORE_DECISION_DETAIL_REQUIRED:{key}")
    return {"status": "SPECIFIED", "detail": detail}


def _assessment(value: object, name: str, *, ready: bool) -> dict[str, str]:
    if not isinstance(value, dict):
        _fail(f"ASSESSMENT_INVALID:{name}")
    raw = cast(dict[str, Any], value)
    status = raw.get("status")
    allowed = {
        "migration_assessment": {"REQUIRED", "NOT_REQUIRED", "NOT_APPLICABLE"},
        "backward_compatibility_assessment": {
            "NO_IMPACT",
            "IMPACT",
            "NOT_APPLICABLE",
        },
        "rollout_assessment": {"REQUIRED", "NOT_REQUIRED", "NOT_APPLICABLE"},
        "rollback_assessment": {"REQUIRED", "NOT_REQUIRED", "NOT_APPLICABLE"},
    }[name]
    if status not in allowed:
        _fail(f"ASSESSMENT_INVALID:{name}")
    stored = {"status": cast(str, status)}
    if not ready:
        return stored
    if status == "NOT_APPLICABLE":
        waiver = raw.get("waiver_evidence")
        if not isinstance(waiver, str) or len(waiver.strip()) < 10:
            _fail(f"WAIVER_REQUIRED:{name}")
        stored["waiver_evidence"] = waiver
    if name == "migration_assessment" and status == "REQUIRED":
        for key in MIGRATION_REQUIRED_KEYS:
            text = raw.get(key)
            if not isinstance(text, str) or not text.strip():
                _fail(f"MIGRATION_KEY_REQUIRED:{key}")
            stored[key] = text
    if name == "backward_compatibility_assessment" and status == "IMPACT":
        for key in ("compatibility_risks", "mitigation_strategy"):
            text = raw.get(key)
            if not isinstance(text, str) or not text.strip():
                _fail(f"COMPATIBILITY_KEY_REQUIRED:{key}")
            stored[key] = text
    if name in {"rollout_assessment", "rollback_assessment"} and status == "REQUIRED":
        procedure = raw.get("procedure")
        if not isinstance(procedure, str) or not procedure.strip():
            _fail(f"PROCEDURE_REQUIRED:{name}")
        stored["procedure"] = procedure
    return stored


def _candidates(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        _fail("CANDIDATES_REQUIRED")
    candidates: list[dict[str, str]] = []
    for item in cast(list[object], value):
        if not isinstance(item, dict):
            _fail("CANDIDATES_REQUIRED")
        raw = cast(dict[str, Any], item)
        ident = raw.get("id")
        summary = raw.get("summary")
        if (
            not isinstance(ident, str)
            or not ident.strip()
            or not isinstance(summary, str)
            or not summary.strip()
        ):
            _fail("CANDIDATES_REQUIRED")
        candidates.append({"id": ident, "summary": summary})
    return candidates


def _rejections(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        _fail("REJECTIONS_INVALID")
    rows: list[dict[str, str]] = []
    for item in cast(list[object], value):
        if not isinstance(item, dict):
            _fail("REJECTIONS_INVALID")
        raw = cast(dict[str, Any], item)
        ident = raw.get("strategy_id")
        reason = raw.get("reason")
        if (
            not isinstance(ident, str)
            or not ident.strip()
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            _fail("REJECTIONS_INVALID")
        rows.append({"strategy_id": ident, "reason": reason})
    return rows


def build_design_document(
    payload: dict[str, Any],
    *,
    change_id: str,
    provenance: dict[str, str],
    requested_status: str,
    block_reason: str | None,
) -> dict[str, Any]:
    """Собрать TS-DESIGN и применить READY-gates только для запрошенного статуса.

    Args:
        payload: Стратегическое тело от исполнителя команды.
        change_id: Идентификатор Change из status-resolved root.
        provenance: Текущие hash входов, вычисленные helper-ом.
        requested_status: ``DRAFT``, ``READY_FOR_REVIEW`` или ``BLOCKED``.
        block_reason: Обязателен для ``BLOCKED``.

    Returns:
        Документ без self-hash и без planning-risk.

    Raises:
        TechnicalStrategyError: Если форма или READY-gates не выполнены.
    """

    if requested_status not in TS_STATUSES:
        _fail("TS_STATUS_INVALID")
    _reject_forbidden_keys(payload)
    ready = requested_status == "READY_FOR_REVIEW"
    if payload.get("change_id") not in {None, change_id}:
        _fail("CHANGE_ID_MISMATCH")
    strategy_class = payload.get("strategy_class")
    if strategy_class not in STRATEGY_CLASSES:
        _fail("STRATEGY_CLASS_INVALID")
    if not isinstance(payload.get("new_architectural_boundary"), bool):
        _fail("FIELD_REQUIRED:new_architectural_boundary")
    candidates = _candidates(payload.get("candidates"))
    recommended = _text(payload, "recommended_strategy_id")
    if recommended not in {item["id"] for item in candidates}:
        _fail("RECOMMENDED_STRATEGY_MISSING")
    rejections = _rejections(payload.get("rejected_strategies_with_reason"))
    boundary = cast(bool, payload["new_architectural_boundary"])
    needs_alternatives = ready and (
        strategy_class
        in {"NEW_ARCHITECTURE", "EXTEND_EXISTING_ARCHITECTURE"}
        or boundary
    )
    if needs_alternatives and (len(candidates) < 2 or not rejections):
        _fail("ALTERNATIVES_REQUIRED")
    decisions_raw = payload.get("core_decisions")
    if not isinstance(decisions_raw, dict):
        _fail("CORE_DECISIONS_REQUIRED")
    decisions = cast(dict[str, Any], decisions_raw)
    assessments_raw = payload.get("assessments")
    if not isinstance(assessments_raw, dict):
        _fail("ASSESSMENTS_REQUIRED")
    assessments = cast(dict[str, Any], assessments_raw)
    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "change_id": change_id,
        "strategy_class": strategy_class,
        "new_architectural_boundary": boundary,
        "candidates": candidates,
        "recommended_strategy_id": recommended,
        "rejected_strategies_with_reason": rejections,
        "core_decisions": {
            key: _core_decision(decisions.get(key), key, ready=ready)
            for key in CORE_DECISIONS
        },
        "assessments": {
            name: _assessment(assessments.get(name), name, ready=ready)
            for name in ASSESSMENT_NAMES
        },
        "constraints": _string_list(payload.get("constraints"), "constraints"),
        "assumptions": _string_list(payload.get("assumptions"), "assumptions"),
        "open_technical_risks": _string_list(
            payload.get("open_technical_risks"), "open_technical_risks"
        ),
        "ts_status": requested_status,
        "upstream_provenance": provenance,
    }
    for key in NARRATIVE_FIELDS:
        document[key] = _text(payload, key)
    if requested_status == "BLOCKED":
        if block_reason not in BLOCK_REASONS:
            _fail("BLOCK_REASON_REQUIRED")
        document["block_reason"] = block_reason
    elif block_reason is not None:
        _fail("BLOCK_REASON_UNEXPECTED")
    _reject_forbidden_keys(document)
    _assert_schema_shape(document)
    return document


def compute_specs_tree_hash(change_root: Path) -> str:
    """Посчитать SPECS_TREE_V1 по markdown-файлам ``specs/``.

    Args:
        change_root: Status-resolved корень Change.

    Returns:
        SHA256 дерева specs.

    Raises:
        TechnicalStrategyError: Если specs markdown отсутствуют.
    """

    specs_dir = change_root / "specs"
    if not specs_dir.is_dir():
        _fail("UPSTREAM_SPECS_MISSING")
    paths = sorted(
        path for path in specs_dir.rglob("*.md") if path.is_file()
    )
    if not paths:
        _fail("UPSTREAM_SPECS_MISSING")
    payload = "SPECS_TREE_V1\n"
    for path in paths:
        relative = path.relative_to(change_root).as_posix()
        payload += f"{relative}\n{_normalized_text(path)}\n"
    return sha256_text(payload)


def input_revision(
    change_id: str,
    owner_hash: str,
    proposal_hash: str,
    specs_hash: str,
) -> str:
    """Связать identity Change с тремя upstream hash.

    Returns:
        ``INPUT_REVISION_SHA256``.
    """

    body = (
        "INPUT_REVISION_V1\n"
        f"{change_id}\n{owner_hash}\n{proposal_hash}\n{specs_hash}\n"
    )
    return sha256_text(body)


def live_provenance(change_root: Path, change_id: str) -> dict[str, str]:
    """Прочитать текущие входы и вернуть provenance без planning-risk.

    Raises:
        TechnicalStrategyError: Если owner-contract, proposal или specs отсутствуют.
    """

    owner = _require_file(
        change_root / "owner-contract.md", "OWNER_CONTRACT_MISSING"
    )
    proposal = _require_file(change_root / "proposal.md", "PROPOSAL_MISSING")
    owner_hash = _file_sha256(owner)
    proposal_hash = _file_sha256(proposal)
    specs_hash = compute_specs_tree_hash(change_root)
    return {
        "change_id": change_id,
        "owner_contract_sha256": owner_hash,
        "proposal_sha256": proposal_hash,
        "specs_sha256": specs_hash,
        "input_revision_sha256": input_revision(
            change_id, owner_hash, proposal_hash, specs_hash
        ),
    }


def _atomic_replace(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            _ = stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _design_markdown(document: dict[str, Any], digest: str) -> str:
    provenance = cast(dict[str, str], document["upstream_provenance"])
    lines = [
        "# Technical Strategy",
        "",
        "TS_DESIGN",
        f"CHANGE_ID: {document['change_id']}",
        f"TS_STATUS: {document['ts_status']}",
        f"STRATEGY_CLASS: {document['strategy_class']}",
        f"TS_DESIGN_SHA256: {digest}",
        f"RECOMMENDED_STRATEGY_ID: {document['recommended_strategy_id']}",
        f"INPUT_REVISION_SHA256: {provenance['input_revision_sha256']}",
        "DESIGN_MD_WRITE: DENIED",
        "OWNER_CONTRACT_WRITER: NO",
        "PLANNING_RISK_WRITER: NO",
        "REVIEW_STATE_WRITER: NO",
        "VERIFIED_STATE: NOT_GRANTED",
        "CONSUMER_INTEGRATION: MISSING",
        "",
        "This artifact does not authorize design.md, apply, or verified state.",
        "",
    ]
    return "\n".join(lines)


def _review_markdown(document: dict[str, Any]) -> str:
    """Собрать машиночитаемый TS-REVIEW markdown без права писать design.md."""

    lines = [
        "# Technical Strategy Review",
        "",
        "TS_REVIEW",
        f"CHANGE_ID: {document['change_id']}",
        f"REVIEW_STATE: {document['review_state']}",
        f"BOUND_TS_DESIGN_SHA256: {document['bound_ts_design_sha256']}",
        f"BOUND_INPUT_REVISION_SHA256: {document['bound_input_revision_sha256']}",
        f"REVIEWER_ID: {document['reviewer_id']}",
        f"FINDING_COUNT: {len(cast(list[object], document['findings']))}",
        f"BLOCKING_FINDING_COUNT: {document['blocking_finding_count']}",
        f"NEXT_ACTION: {document['next_action']}",
        "DESIGN_MD_WRITE: DENIED",
        "TS_DESIGN_WRITER: NO",
        "VERIFIED_STATE: NOT_GRANTED",
        "DESIGN_UNLOCK: NOT_WIRED",
        "CONSUMER_INTEGRATION: MISSING",
        "SOURCE_PARITY: PARTIAL",
        "",
        "PASS is current only for zero blocking findings and the live design bind.",
        "PASS does not unlock design.md. /opsx-new, /opsx-ff, and /opsx-continue",
        "do not consume this receipt.",
        "",
    ]
    block_reason = document.get("block_reason")
    if isinstance(block_reason, str):
        lines.insert(10, f"BLOCK_REASON: {block_reason}")
    return "\n".join(lines)


def _review_next_action(
    state: str,
    block_reason: str | None,
    findings: list[dict[str, Any]],
) -> str:
    """Вернуть точное восстановление, не открывая design.md."""

    if state == "PASS":
        return "STOP_DESIGN_UNLOCK_NOT_WIRED"
    if block_reason == "TS_STATUS_NOT_READY":
        return "REPAIR_TS_DESIGN"
    if block_reason in {"INPUT_REVISION_STALE", "CROSS_REVISION_REUSE"}:
        return "RECONCILE_TS_DESIGN"
    if any(item["type"] == "OWNER_SEMANTIC_GAP" for item in findings):
        return "UPSTREAM_SEMANTIC_REALIGNMENT_REQUIRED"
    if state == "DEFECTS_FOUND":
        return "REPAIR_TS_DESIGN"
    return "REPAIR_TS_REVIEW"


def _assert_review_schema(document: dict[str, Any]) -> None:
    """Проверить required-ключи ``ts_review.schema.json`` без внешнего validator.

    Raises:
        TechnicalStrategyError: Если schema или документ неполны.
    """

    try:
        raw = json.loads(REVIEW_SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TechnicalStrategyError("SCHEMA_INVALID") from error
    if not isinstance(raw, dict):
        _fail("SCHEMA_INVALID")
    schema = cast(dict[str, Any], raw)
    required = schema.get("required")
    if not isinstance(required, list) or not required:
        _fail("SCHEMA_INVALID")
    for key in cast(list[object], required):
        if not isinstance(key, str) or key not in document:
            _fail(f"SCHEMA_REQUIRED:{key}")
    if document.get("design_unlock") != "NOT_WIRED":
        _fail("DESIGN_UNLOCK_FORBIDDEN")
    if document.get("verified_state") != "NOT_GRANTED":
        _fail("VERIFIED_STATE_FORBIDDEN")


def _write_pair(directory: Path, stem: str, document: dict[str, Any], markdown: str) -> None:
    json_path = _within(directory, directory / f"{stem}.json", "WRITE_BOUNDARY")
    md_path = _within(directory, directory / f"{stem}.md", "WRITE_BOUNDARY")
    body = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _atomic_replace(json_path, body)
    _atomic_replace(md_path, markdown)


def publish_design(args: argparse.Namespace) -> dict[str, Any]:
    """Проверить payload и атомарно заменить только ``TS-DESIGN.*``.

    Returns:
        Машиночитаемый результат без записи review или design.md.

    Raises:
        TechnicalStrategyError: Если READY-gates или входы не выполнены.
    """

    change_id = cast(str, args.change_id)
    if CHANGE_ID_PATTERN.fullmatch(change_id) is None:
        _fail("INVALID_CHANGE_ID")
    change_root = _require_dir(cast(Path, args.change_root), "INVALID_CHANGE_ROOT")
    payload = _load_json_object(cast(Path, args.payload), "PAYLOAD_INVALID")
    provenance = live_provenance(change_root, change_id)
    if provenance["change_id"] != change_id:
        _fail("CHANGE_ID_MISMATCH")
    _reject_forbidden_keys(payload)
    _reject_stale_claim(payload, provenance)
    document = build_design_document(
        payload,
        change_id=change_id,
        provenance=provenance,
        requested_status=cast(str, args.requested_status),
        block_reason=cast(str | None, args.block_reason),
    )
    digest = ts_design_sha256(document)
    ts_dir = change_root / "ts"
    review_path = ts_dir / "TS-REVIEW.json"
    prior_review = review_path.read_bytes() if review_path.is_file() else None
    _write_pair(ts_dir, "TS-DESIGN", document, _design_markdown(document, digest))
    if review_path.is_file() and review_path.read_bytes() != prior_review:
        _fail("REVIEW_MUTATED_BY_DESIGN_WRITER")
    prior = "ABSENT"
    if prior_review is not None:
        prior = "CURRENT_PASS" if current_pass(change_root) else "STALE_NOT_PASS"
    return {
        "STATUS": "TS_DESIGN_PUBLISHED",
        "CHANGE_ID": change_id,
        "TS_STATUS": document["ts_status"],
        "TS_DESIGN_SHA256": digest,
        "INPUT_REVISION_SHA256": provenance["input_revision_sha256"],
        "PRIOR_TS_REVIEW": prior,
        "FILES_CHANGED": "ts/TS-DESIGN.md, ts/TS-DESIGN.json",
        "VERIFIED_STATE": "NOT_GRANTED",
    }


def _design_is_fresh(document: dict[str, Any], provenance: dict[str, str]) -> bool:
    stored = document.get("upstream_provenance")
    if not isinstance(stored, dict):
        return False
    return cast(dict[str, Any], stored) == provenance


def publish_review(args: argparse.Namespace) -> dict[str, Any]:
    """Прочитать текущий TS-DESIGN и записать только независимый TS-REVIEW.

    ``PASS`` возможен только при пустых blocking findings, ``READY_FOR_REVIEW``,
    совпадении evidence с hash текущего design и live input revision.
    Evidence не может само назначить ``review_state``. Design bytes не меняются.

    Returns:
        Машиночитаемый результат review.

    Raises:
        TechnicalStrategyError: Если независимое ревью или design отсутствуют.
    """

    change_id = cast(str, args.change_id)
    if CHANGE_ID_PATTERN.fullmatch(change_id) is None:
        _fail("INVALID_CHANGE_ID")
    reviewer_arg = cast(str | None, args.reviewer_id)
    findings_path = cast(Path | None, args.findings)
    if findings_path is None or not reviewer_arg:
        _fail("INDEPENDENT_REVIEW_REQUIRED")
    change_root = _require_dir(cast(Path, args.change_root), "INVALID_CHANGE_ROOT")
    design_path = _require_file(
        change_root / "ts" / "TS-DESIGN.json", "TS_DESIGN_MISSING"
    )
    before = design_path.read_bytes()
    design = _load_json_object(design_path, "TS_DESIGN_INVALID")
    if design.get("change_id") != change_id:
        _fail("CHANGE_ID_MISMATCH")
    evidence, findings = _load_review_evidence(findings_path, reviewer_arg)
    provenance = live_provenance(change_root, change_id)
    digest = ts_design_sha256(design)
    stored_provenance = design.get("upstream_provenance")
    stored_revision = ""
    if isinstance(stored_provenance, dict):
        candidate = cast(dict[str, Any], stored_provenance).get(
            "input_revision_sha256"
        )
        if isinstance(candidate, str):
            stored_revision = candidate
    block_reason: str | None = None
    if (
        evidence["bound_ts_design_sha256"] != digest
        or evidence["bound_input_revision_sha256"] != stored_revision
    ):
        state = "BLOCKED"
        block_reason = "CROSS_REVISION_REUSE"
        findings = []
    elif design.get("ts_status") != "READY_FOR_REVIEW":
        state = "BLOCKED"
        block_reason = "TS_STATUS_NOT_READY"
    elif not _design_is_fresh(design, provenance):
        state = "BLOCKED"
        block_reason = "INPUT_REVISION_STALE"
    elif findings:
        state = "DEFECTS_FOUND"
    else:
        try:
            _ = build_design_document(
                design,
                change_id=change_id,
                provenance=provenance,
                requested_status="READY_FOR_REVIEW",
                block_reason=None,
            )
        except TechnicalStrategyError as error:
            state = "BLOCKED"
            block_reason = f"SCHEMA_VIOLATION:{error}"
        else:
            state = "PASS"
    if state not in REVIEW_STATES:
        _fail("REVIEW_STATE_INVALID")
    blocking = sum(1 for item in findings if item["severity"] == "BLOCKING")
    if state == "PASS" and (findings or blocking):
        _fail("PASS_WITH_FINDINGS")
    if state == "BLOCKED" and not block_reason:
        _fail("BLOCK_REASON_REQUIRED")
    if state == "PASS" and block_reason is not None:
        _fail("BLOCK_REASON_UNEXPECTED")
    review: dict[str, Any] = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "contract_id": REVIEW_CONTRACT_ID,
        "change_id": change_id,
        "review_state": state,
        "reviewer_id": evidence["reviewer_id"],
        "independence": INDEPENDENCE_TOKEN,
        "bound_ts_design_sha256": digest,
        "bound_input_revision_sha256": provenance["input_revision_sha256"],
        "design_input_revision_sha256": stored_revision,
        "source_input_identities": {
            "owner_contract_sha256": provenance["owner_contract_sha256"],
            "proposal_sha256": provenance["proposal_sha256"],
            "specs_sha256": provenance["specs_sha256"],
        },
        "blocking_finding_count": blocking,
        "design_md_write": "DENIED",
        "verified_state": "NOT_GRANTED",
        "design_unlock": "NOT_WIRED",
        "consumer_integration": "MISSING",
        "source_parity": "PARTIAL",
        "findings": findings,
        "ts_design_writer": "NO",
        "next_action": _review_next_action(state, block_reason, findings),
    }
    if block_reason is not None:
        review["block_reason"] = block_reason
    _assert_review_schema(review)
    _write_pair(
        change_root / "ts", "TS-REVIEW", review, _review_markdown(review)
    )
    if design_path.read_bytes() != before:
        _fail("DESIGN_MUTATED_BY_REVIEW")
    return {
        "STATUS": "TS_REVIEW_PUBLISHED",
        "CHANGE_ID": change_id,
        "REVIEW_STATE": state,
        "BOUND_TS_DESIGN_SHA256": digest,
        "BOUND_INPUT_REVISION_SHA256": provenance["input_revision_sha256"],
        "REVIEWER_ID": evidence["reviewer_id"],
        "FINDING_COUNT": len(findings),
        "BLOCKING_FINDING_COUNT": blocking,
        "BLOCK_REASON": block_reason or "",
        "NEXT_ACTION": review["next_action"],
        "FILES_CHANGED": "ts/TS-REVIEW.md, ts/TS-REVIEW.json",
        "VERIFIED_STATE": "NOT_GRANTED",
        "DESIGN_UNLOCK": "NOT_WIRED",
        "CONSUMER_INTEGRATION": "MISSING",
        "SOURCE_PARITY": "PARTIAL",
    }


def current_pass(change_root: Path) -> bool:
    """Вернуть True только для PASS, связанного с текущими design bytes.

    Сырой ``review_state: PASS`` без совпадения hash и input revision не
    считается текущим.
    """

    review_path = change_root / "ts" / "TS-REVIEW.json"
    design_path = change_root / "ts" / "TS-DESIGN.json"
    if not review_path.is_file() or not design_path.is_file():
        return False
    try:
        review = _load_json_object(review_path, "TS_REVIEW_INVALID")
        design = _load_json_object(design_path, "TS_DESIGN_INVALID")
        change_id = design.get("change_id")
        if not isinstance(change_id, str):
            return False
        provenance = live_provenance(change_root, change_id)
    except TechnicalStrategyError:
        return False
    reviewer = review.get("reviewer_id")
    return (
        review.get("review_state") == "PASS"
        and review.get("findings") == []
        and review.get("blocking_finding_count") == 0
        and review.get("independence") == INDEPENDENCE_TOKEN
        and isinstance(reviewer, str)
        and reviewer.strip().casefold() not in FORBIDDEN_REVIEWER_IDS
        and review.get("verified_state") == "NOT_GRANTED"
        and review.get("design_unlock") == "NOT_WIRED"
        and review.get("consumer_integration") == "MISSING"
        and review.get("bound_ts_design_sha256") == ts_design_sha256(design)
        and review.get("bound_input_revision_sha256")
        == provenance["input_revision_sha256"]
        and _design_is_fresh(design, provenance)
        and design.get("ts_status") == "READY_FOR_REVIEW"
    )


def design_freshness(args: argparse.Namespace) -> dict[str, Any]:
    """Сравнить сохранённый TS-DESIGN с live inputs, ничего не записывая.

    Returns:
        ``ABSENT``, ``CURRENT``, ``STALE``, ``INVALID`` или
        ``CHANGE_ID_MISMATCH``.

    Raises:
        TechnicalStrategyError: Если change root или upstream inputs непригодны.
    """

    change_id = cast(str, args.change_id)
    if CHANGE_ID_PATTERN.fullmatch(change_id) is None:
        _fail("INVALID_CHANGE_ID")
    change_root = _require_dir(cast(Path, args.change_root), "INVALID_CHANGE_ROOT")
    provenance = live_provenance(change_root, change_id)
    result: dict[str, Any] = {
        "STATUS": "TS_DESIGN_FRESHNESS",
        "CHANGE_ID": change_id,
        "LIVE_INPUT_REVISION_SHA256": provenance["input_revision_sha256"],
        "FILES_CHANGED": "none",
        "VERIFIED_STATE": "NOT_GRANTED",
        "TS_REVIEW_ACTIVATED": "NO",
    }
    design_path = change_root / "ts" / "TS-DESIGN.json"
    if not design_path.is_file():
        result["FRESHNESS"] = "ABSENT"
        return result
    design = _load_json_object(design_path, "TS_DESIGN_INVALID")
    if design.get("change_id") != change_id:
        result["FRESHNESS"] = "CHANGE_ID_MISMATCH"
        return result
    digest = ts_design_sha256(design)
    recorded = ""
    markdown_path = change_root / "ts" / "TS-DESIGN.md"
    if markdown_path.is_file():
        for line in _normalized_text(markdown_path).splitlines():
            if line.startswith("TS_DESIGN_SHA256: "):
                recorded = line.split(": ", 1)[1].strip()
    if recorded != digest:
        result["FRESHNESS"] = "INVALID"
        result["REASON"] = "HASH_MISMATCH"
        return result
    if not _design_is_fresh(design, provenance):
        result["FRESHNESS"] = "STALE"
        result["REASON"] = "INPUT_REVISION_STALE"
        return result
    result["FRESHNESS"] = "CURRENT"
    result["TS_STATUS"] = design.get("ts_status")
    result["TS_DESIGN_SHA256"] = digest
    return result


def read_status(args: argparse.Namespace) -> dict[str, Any]:
    """Показать freshness без записи файлов."""

    change_root = _require_dir(cast(Path, args.change_root), "INVALID_CHANGE_ROOT")
    change_id = cast(str, args.change_id)
    if CHANGE_ID_PATTERN.fullmatch(change_id) is None:
        _fail("INVALID_CHANGE_ID")
    design_path = change_root / "ts" / "TS-DESIGN.json"
    reason = "NOT_CURRENT_PASS"
    if design_path.is_file():
        design = _load_json_object(design_path, "TS_DESIGN_INVALID")
        if design.get("change_id") != change_id:
            reason = "CHANGE_ID_MISMATCH"
    fresh = reason != "CHANGE_ID_MISMATCH" and current_pass(change_root)
    if fresh:
        reason = "CURRENT"
    return {
        "STATUS": "TS_REVIEW_STATUS",
        "CURRENT_PASS": fresh,
        "REASON": reason,
        "FILES_CHANGED": "none",
        "VERIFIED_STATE": "NOT_GRANTED",
        "DESIGN_UNLOCK": "NOT_WIRED",
    }


def build_parser() -> argparse.ArgumentParser:
    """Собрать CLI design, review и read-only status."""

    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "action", choices=("design", "freshness", "review", "status")
    )
    _ = parser.add_argument("--change-id", required=True)
    _ = parser.add_argument("--change-root", type=Path, required=True)
    _ = parser.add_argument("--payload", type=Path)
    _ = parser.add_argument(
        "--requested-status",
        choices=tuple(sorted(TS_STATUSES)),
    )
    _ = parser.add_argument("--block-reason", choices=tuple(sorted(BLOCK_REASONS)))
    _ = parser.add_argument("--findings", type=Path)
    _ = parser.add_argument("--reviewer-id")
    return parser


def main() -> int:
    """Опубликовать один артефакт или прочитать freshness."""

    args = build_parser().parse_args()
    if args.action == "design":
        if (
            args.payload is None
            or args.requested_status is None
            or args.findings
            or args.reviewer_id
        ):
            _fail("DESIGN_ARGUMENTS_INVALID")
        result = publish_design(args)
    elif args.action == "freshness":
        if (
            args.payload
            or args.findings
            or args.requested_status
            or args.block_reason
            or args.reviewer_id
        ):
            _fail("FRESHNESS_ARGUMENTS_INVALID")
        result = design_freshness(args)
    elif args.action == "review":
        if args.payload or args.requested_status or args.block_reason:
            _fail("REVIEW_ARGUMENTS_INVALID")
        if args.findings is None or not args.reviewer_id:
            _fail("INDEPENDENT_REVIEW_REQUIRED")
        result = publish_review(args)
    else:
        if (
            args.payload
            or args.findings
            or args.requested_status
            or args.block_reason
            or args.reviewer_id
        ):
            _fail("STATUS_ARGUMENTS_INVALID")
        result = read_status(args)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TechnicalStrategyError as error:
        raise SystemExit(f"TECHNICAL_STRATEGY_ERROR: {error}") from error
