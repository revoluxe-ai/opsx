"""Пересчитать INPUTS и SCENARIO_FILES в plan.receipt.json (maintenance, mode=plan parity).

Обновляет относительные пути, content_hash спек/design и PLAN_CONTENT_HASH без
смены PLAN_EVIDENCE_ID и тел сценариев.
"""
from __future__ import annotations

from opsx.project_context import resource_path

import argparse
import hashlib
import json
import re
from pathlib import Path

from agents.common.scenario_files import (
    file_sha256,
    plan_content_hash,
    plan_receipt_machine_path,
    scenario_filename,
)
from agents.common.scenario_migrate import iter_coverage_dirs

_ROW = re.compile(r"^\s*-\s+SCENARIO_ID:\s*(.*?)\s*$")


def _lf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def _hash_file(path: Path) -> str:
    return hashlib.sha256(_lf(path.read_bytes())).hexdigest()


def _scenario_id_from_file(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _ROW.match(line)
        if match:
            return match.group(1)
    raise ValueError(f"SCENARIO_ID missing: {path}")


def _build_inputs(change: Path) -> dict:
    """Собрать INPUTS с путями относительно каталога change."""
    inputs: dict = {}
    design = change / "design.md"
    if design.is_file():
        h = _hash_file(design)
        inputs["DESIGN_REVISION"] = {"path": "design.md", "content_hash": h}
        inputs["LIFECYCLE_REVISION"] = {"source": "design.md", "content_hash": h}
    specs = sorted((change / "specs").rglob("*.md")) if (change / "specs").is_dir() else []
    if specs:
        inputs["SPEC_REVISION"] = [
            {
                "path": p.relative_to(change).as_posix(),
                "content_hash": _hash_file(p),
            }
            for p in specs
        ]
    owner = change / "owner-contract.md"
    if owner.is_file():
        inputs["OWNER_CONTRACT_REVISION"] = {
            "path": "owner-contract.md",
            "content_hash": _hash_file(owner),
        }
    return inputs


def _rebuild_scenario_files(coverage: Path) -> list[dict]:
    """Пересобрать SCENARIO_FILES по каталогу scenarios/."""
    folder = coverage / "scenarios"
    if not folder.is_dir():
        return []
    entries: list[dict] = []
    for path in sorted(folder.glob("*.md")):
        scenario_id = _scenario_id_from_file(path)
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


def _normalize_inputs_paths(change: Path, inputs: dict) -> dict:
    """Починить абсолютные openspec/changes/... пути в сохранённом receipt."""
    prefix = f"openspec/changes/{change.name}/"

    def fix_path(rel: str) -> str:
        if rel.startswith(prefix):
            return rel[len(prefix):]
        return rel

    out = dict(inputs)
    for key in ("DESIGN_REVISION", "OWNER_CONTRACT_REVISION"):
        block = out.get(key)
        if isinstance(block, dict) and isinstance(block.get("path"), str):
            block = dict(block)
            block["path"] = fix_path(block["path"])
            out[key] = block
    life = out.get("LIFECYCLE_REVISION")
    if isinstance(life, dict) and isinstance(life.get("source"), str):
        life = dict(life)
        life["source"] = fix_path(life["source"])
        out["LIFECYCLE_REVISION"] = life
    specs = out.get("SPEC_REVISION")
    if isinstance(specs, list):
        out["SPEC_REVISION"] = [
            {
                **item,
                "path": fix_path(str(item.get("path", ""))),
            }
            if isinstance(item, dict) else item
            for item in specs
        ]
    return out


def refresh_receipt(repo: Path, change_id: str) -> list[str]:
    """Обновить receipt. Вернуть список изменённых полей."""
    change = resource_path(repo, "openspec/changes", change_id)
    coverage = change / "scenario-coverage"
    receipt_path = plan_receipt_machine_path(coverage)
    if not receipt_path.is_file():
        return ["MISSING_RECEIPT"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if not isinstance(receipt, dict):
        raise ValueError("COVERAGE_RECEIPT_INVALID")
    changed: list[str] = []
    entries = _rebuild_scenario_files(coverage)
    if entries != receipt.get("SCENARIO_FILES"):
        receipt["SCENARIO_FILES"] = entries
        changed.append("SCENARIO_FILES")
    if receipt.get("SCENARIO_COUNT") != len(entries):
        receipt["SCENARIO_COUNT"] = len(entries)
        changed.append("SCENARIO_COUNT")
    inputs = _build_inputs(change)
    if not inputs:
        changed.append("INPUTS_EMPTY")
    else:
        receipt["INPUTS"] = inputs
        receipt["GENERATED_FROM"] = json.loads(json.dumps(inputs))
        changed.append("INPUTS")
    if isinstance(receipt.get("INPUTS"), dict):
        receipt["INPUTS"] = _normalize_inputs_paths(change, receipt["INPUTS"])
    plan_bytes = (coverage / "plan.md").read_bytes()
    pairs = [(item["scenario_id"], item["content_hash"]) for item in entries]
    plan_hash = plan_content_hash(plan_bytes, pairs)
    if receipt.get("PLAN_CONTENT_HASH") != plan_hash:
        receipt["PLAN_CONTENT_HASH"] = plan_hash
        changed.append("PLAN_CONTENT_HASH")
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return changed or ["ALREADY_CURRENT"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh scenario plan receipt INPUTS and hashes")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("change_id", nargs="*", help="Change ids (default: all coverage dirs)")
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    changes = args.change_id or [
        p.parent.name for p in iter_coverage_dirs(repo)
    ]
    failures = 0
    for change_id in changes:
        try:
            changed = refresh_receipt(repo, change_id)
            print(f"OK {change_id}: {', '.join(changed)}")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            failures += 1
            print(f"FAIL {change_id}: {exc}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
