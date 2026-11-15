"""Загрузка durable plan evidence для scenario-coverage audit (без LLM)."""

from __future__ import annotations

from opsx.project_context import resource_path

import json
import logging
import re
from pathlib import Path
from typing import Any

from agents.common.scenario_files import (
    plan_has_embedded_rows,
    plan_receipt_machine_path,
    read_scenario_ids,
    scenario_filename,
)
from agents.verify.impl_verify.preflight import change_root

_LOGGER = logging.getLogger(__name__)

_SCENARIO_HEADING = re.compile(r"^#{1,3}\s+Scenario\s*[:：]?\s*(.+)$", re.MULTILINE | re.IGNORECASE)


def _read_bounded(path: Path, *, max_chars: int | None = None) -> str | None:
    """Прочитать файл coverage целиком. ``max_chars`` не режет текст."""
    _ = max_chars
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def load_scenario_coverage_plan_bundle(
    change_id: str,
    *,
    repo_root: Path,
) -> dict[str, Any]:
    """Собрать plan.md, plan.receipt.json и заголовки Scenario из delta specs change.

    Args:
        change_id: Идентификатор OpenSpec change.
        repo_root: Корень репозитория (``openspec/changes/…``).

    Returns:
        Словарь для INPUT CONTEXT промпта и детерминированного preflight.
    """
    root = change_root(change_id, repo_root=repo_root)
    coverage_dir = resource_path(root, "scenario-coverage")
    plan_path = coverage_dir / "plan.md"
    receipt_path = plan_receipt_machine_path(coverage_dir)

    bundle: dict[str, Any] = {
        "change_id": change_id,
        "plan_path": str(plan_path.relative_to(repo_root)).replace("\\", "/"),
        "receipt_path": str(receipt_path.relative_to(repo_root)).replace("\\", "/"),
        "plan_text": None,
        "plan_receipt": None,
        "scenario_ids": [],
        "scenario_files": [],
        "missing_artifacts": [],
        "spec_scenario_headings": [],
    }

    plan_text = _read_bounded(plan_path)
    if plan_text is None:
        bundle["missing_artifacts"].append("scenario-coverage/plan.md")
    else:
        bundle["plan_text"] = plan_text
        if plan_has_embedded_rows(plan_path.read_text(encoding="utf-8")):
            bundle["layout_pause"] = (
                "COVERAGE_PLAN_EMBEDDED: тела SCENARIO_ID остались в plan.md"
            )
            bundle["missing_artifacts"].append("scenario-coverage/plan.md (embedded scenario rows)")
        else:
            try:
                ids = read_scenario_ids(coverage_dir, allow_historical=False)
            except ValueError as exc:
                bundle["layout_pause"] = str(exc)
                bundle["missing_artifacts"].append(f"scenario-coverage/scenarios ({exc})")
            else:
                bundle["scenario_ids"] = ids
                files: list[dict[str, str]] = []
                for scenario_id in ids:
                    rel = f"scenarios/{scenario_filename(scenario_id)}"
                    body = _read_bounded(coverage_dir / rel, max_chars=20_000)
                    files.append({
                        "scenario_id": scenario_id,
                        "path": f"openspec/changes/{change_id}/scenario-coverage/{rel}",
                        "text": body or "",
                    })
                bundle["scenario_files"] = files

    if not receipt_path.is_file():
        bundle["missing_artifacts"].append(
            f"opsx/receipts/{change_id}/scenario-coverage/plan.receipt.json",
        )
    else:
        try:
            raw = json.loads(receipt_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            bundle["receipt_parse_error"] = str(exc)
            bundle["missing_artifacts"].append("scenario-coverage/plan.receipt.json (invalid JSON)")
        else:
            if isinstance(raw, dict):
                bundle["plan_receipt"] = raw
            else:
                bundle["missing_artifacts"].append(
                    "scenario-coverage/plan.receipt.json (not an object)",
                )

    specs_dir = resource_path(root, "specs")
    if specs_dir.is_dir():
        headings: list[dict[str, str]] = []
        for spec_file in sorted(specs_dir.rglob("*.md")):
            rel = str(spec_file.relative_to(repo_root)).replace("\\", "/")
            content = _read_bounded(spec_file, max_chars=80_000)
            if not content:
                continue
            for match in _SCENARIO_HEADING.finditer(content):
                title = match.group(1).strip()
                if title:
                    headings.append({"spec_path": rel, "scenario_title": title})
            if len(headings) >= 40:
                break
        bundle["spec_scenario_headings"] = headings[:40]

    if bundle["missing_artifacts"]:
        _LOGGER.info(
            "Scenario coverage plan bundle incomplete for %s: %s",
            change_id,
            bundle["missing_artifacts"],
        )
    return bundle


def assess_plan_materialization(bundle: dict[str, Any]) -> str | None:
    """Вернуть причину BLOCKED или ``None``, если audit можно запускать.

    Args:
        bundle: Результат ``load_scenario_coverage_plan_bundle``.

    Returns:
        Текст block_reason для UNKNOWN или ``None``.
    """
    if bundle.get("layout_pause"):
        return str(bundle["layout_pause"])
    missing = bundle.get("missing_artifacts") or []
    if missing:
        joined = ", ".join(str(item) for item in missing)
        return f"Отсутствуют durable plan artifacts: {joined}"
    receipt = bundle.get("plan_receipt")
    if not isinstance(receipt, dict):
        return "plan.receipt.json не загружен"
    if not receipt.get("SCENARIO_COVERAGE_RECEIPT"):
        return "plan.receipt.json не помечен SCENARIO_COVERAGE_RECEIPT"
    plan_status = str(receipt.get("PLAN_STATUS") or "").strip().lower()
    if plan_status not in {"ready", "provisional"}:
        return f"PLAN_STATUS={receipt.get('PLAN_STATUS')!r} не пригоден для audit"
    return None
