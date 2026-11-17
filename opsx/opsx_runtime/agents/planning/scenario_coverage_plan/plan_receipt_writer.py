"""Детерминированная запись machine ``plan.receipt.json`` в ``opsx/receipts/``."""
from __future__ import annotations

from opsx.project_context import resource_path

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from agents.common.scenario_files import (
    file_sha256,
    plan_content_hash,
    plan_receipt_machine_path,
    scenario_filename,
)
from check_workflow_receipt import CheckWorkflowReceipt

_ROW = re.compile(r"^\s*-\s+SCENARIO_ID:\s*(.*?)\s*$")
_ROW_PLAIN = re.compile(r"^\s*SCENARIO_ID:\s*(.*?)\s*$")


def _hash_file(path: Path) -> str:
    """sha256 LF-нормализованных байт файла для поля INPUTS."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def build_plan_receipt_inputs(change: Path) -> dict[str, Any]:
    """Собрать блок INPUTS с путями относительно каталога change."""
    inputs: dict[str, Any] = {}
    design = change / "design.md"
    if design.is_file():
        digest = _hash_file(design)
        inputs["DESIGN_REVISION"] = {"path": "design.md", "content_hash": digest}
        inputs["LIFECYCLE_REVISION"] = {"source": "design.md", "content_hash": digest}
    specs = sorted((change / "specs").rglob("*.md")) if (change / "specs").is_dir() else []
    if specs:
        inputs["SPEC_REVISION"] = [
            {
                "path": path.relative_to(change).as_posix(),
                "content_hash": _hash_file(path),
            }
            for path in specs
        ]
    owner = change / "owner-contract.md"
    if owner.is_file():
        inputs["OWNER_CONTRACT_REVISION"] = {
            "path": "owner-contract.md",
            "content_hash": _hash_file(owner),
        }
    return inputs


def _scenario_file_entries(coverage: Path) -> list[dict[str, Any]]:
    """Пересобрать SCENARIO_FILES по каталогу ``scenarios/`` в порядке имени файла."""
    folder = coverage / "scenarios"
    if not folder.is_dir():
        return []
    entries: list[dict[str, Any]] = []
    for path in sorted(folder.glob("*.md")):
        scenario_id = ""
        for line in path.read_text(encoding="utf-8").splitlines():
            match = _ROW.match(line) or _ROW_PLAIN.match(line)
            if match:
                scenario_id = match.group(1)
                break
        if not scenario_id:
            raise ValueError(f"SCENARIO_ID missing: {path}")
        expected_name = scenario_filename(scenario_id)
        if path.name != expected_name:
            raise ValueError(f"SCENARIO_FILE_NAME_MISMATCH: {path.name} vs {expected_name}")
        data = path.read_bytes()
        entries.append({
            "scenario_id": scenario_id,
            "path": f"scenarios/{path.name}",
            "content_hash": file_sha256(data),
        })
    return entries


def _plan_evidence_id(change_id: str, plan_hash: str, inputs: dict[str, Any]) -> str:
    """Детерминированный PLAN_EVIDENCE_ID без временных меток."""
    payload = json.dumps(
        {"change_id": change_id, "plan_content_hash": plan_hash, "inputs": inputs},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _inputs_current(change: Path, inputs: dict[str, Any]) -> bool:
    """True, если все pins INPUTS совпадают с диском."""
    for value in inputs.values():
        sources: list[dict[str, Any]] = []
        if isinstance(value, list):
            sources.extend(x for x in value if isinstance(x, dict))
        elif isinstance(value, dict) and (value.get("path") or value.get("source")):
            sources.append(value)
        for entry in sources:
            rel = entry.get("path") or entry.get("source")
            digest = entry.get("content_hash")
            if not isinstance(rel, str) or not isinstance(digest, str):
                return False
            file_path = (change / rel).resolve()
            if not file_path.is_relative_to(change.resolve()) or not file_path.is_file():
                return False
            if not CheckWorkflowReceipt.matches_file(file_path, digest):
                return False
    return True


def commit_plan_machine_receipt(repo_root: Path, change_id: str) -> Path:
    """Записать или обновить канонический ``plan.receipt.json`` после mutating plan hop.

    Args:
        repo_root: Корень репозитория.
        change_id: Идентификатор OpenSpec change.

    Returns:
        Абсолютный путь записанного receipt.

    Raises:
        ValueError: Нет ``plan.md`` или невалидная раскладка сценариев.
        OSError: Ошибка записи на диск.
    """
    root = repo_root.resolve()
    change = resource_path(root, "openspec", "changes", change_id)
    coverage = change / "scenario-coverage"
    plan_path = coverage / "plan.md"
    if not plan_path.is_file():
        raise ValueError("COVERAGE_PLAN_MISSING")
    receipt_path = plan_receipt_machine_path(coverage)
    entries = _scenario_file_entries(coverage)
    inputs = build_plan_receipt_inputs(change)
    plan_bytes = plan_path.read_bytes()
    pairs = [(item["scenario_id"], item["content_hash"]) for item in entries]
    plan_hash = plan_content_hash(plan_bytes, pairs)

    existing: dict[str, Any] = {}
    materialization = "COMMITTED"
    if receipt_path.is_file():
        try:
            loaded = json.loads(receipt_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing = loaded
        except json.JSONDecodeError:
            existing = {}
    old_id = existing.get("PLAN_EVIDENCE_ID")
    if (
        isinstance(old_id, str)
        and old_id
        and existing.get("PLAN_CONTENT_HASH") == plan_hash
        and existing.get("INPUTS") == inputs
        and existing.get("SCENARIO_FILES") == entries
    ):
        evidence_id = old_id
        materialization = "ALREADY_CURRENT"
    else:
        evidence_id = _plan_evidence_id(change_id, plan_hash, inputs)

    if not inputs or not _inputs_current(change, inputs):
        raise ValueError("COVERAGE_INPUTS_STALE")

    plan_rel = f"openspec/changes/{change_id}/scenario-coverage/plan.md"
    receipt_rel = f"opsx/receipts/{change_id}/scenario-coverage/plan.receipt.json"
    receipt: dict[str, Any] = {
        "SCHEMA_VERSION": 1,
        "SCENARIO_COVERAGE_RECEIPT": True,
        "CHANGE_ID": change_id,
        "MODE": "plan",
        "PLAN_STATUS": "ready",
        "PLAN_EVIDENCE_ID": evidence_id,
        "PLAN_CONTENT_HASH": plan_hash,
        "PLAN_PATH": plan_rel,
        "RECEIPT_PATH": receipt_rel,
        "SCENARIO_FILES": entries,
        "SCENARIO_COUNT": len(entries),
        "INPUTS": inputs,
        "GENERATED_FROM": json.loads(json.dumps(inputs)),
        "MATERIALIZATION_STATUS": materialization,
        "LIFECYCLE_COVERAGE": existing.get("LIFECYCLE_COVERAGE", {"covered": [], "gaps": []}),
        "UNRESOLVED_GAPS": existing.get("UNRESOLVED_GAPS", []),
    }
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(receipt, ensure_ascii=False, indent=2) + "\n"
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    receipt_path.write_text(normalized, encoding="utf-8", newline="\n")
    return receipt_path


__all__ = ["build_plan_receipt_inputs", "commit_plan_machine_receipt"]
