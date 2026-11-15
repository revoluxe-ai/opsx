#!/usr/bin/env python3
"""Генератор UX v9 transition registry @ BASE_SHA (consultation-funnel-baseline).

Назначение: читает vendored ``ux_state_contract.json`` @ pin BASE_SHA
(``tools/openspec/consultation_baseline/pinned/`` documented byte pin), эмитит change-local
``ux-v9-transition-registry.jsonl`` и
``registry.manifest.json``. Не изменяет production ``invest_yalta`` runtime.

Выходы: ``openspec/changes/consultation-funnel-baseline/evidence/``.

Связь: design D-BL-01, decision ux-v9-transition-registry-verification.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from tools.openspec.consultation_baseline._constants import (
    BASE_SHA,
    CONTRACT_GIT_PATH,
    JSONL_FILENAME,
    MANIFEST_FILENAME,
    evidence_dir,
    repo_root_from_here,
)
from tools.openspec.consultation_baseline._classification_summary import (
    write_classification_summary,
)
from tools.openspec.consultation_baseline._classify_runtime import classify_registry_rows
from tools.openspec.consultation_baseline._registry_build import (
    count_transitions,
    iter_registry_rows,
    state_ids_from_contract,
)
from tools.openspec.consultation_baseline._source_bytes import (
    load_contract_bytes,
    parse_contract,
    sha256_bytes,
)


class ExportError(RuntimeError):
    """Ошибка генерации реестра (blocking)."""


def export_registry(
    repo_root: Path,
    base_sha: str = BASE_SHA,
    out_dir: Path | None = None,
) -> tuple[Path, Path]:
    """Сгенерировать jsonl и manifest.

    Args:
        repo_root: корень репозитория.
        base_sha: git pin для исходного контракта.
        out_dir: каталог evidence (по умолчанию change-local).

    Returns:
        Пути (jsonl_path, manifest_path).

    Raises:
        ExportError: несоответствие 63/392 или структура контракта.
    """

    target_dir = out_dir if out_dir is not None else evidence_dir(repo_root)
    target_dir.mkdir(parents=True, exist_ok=True)

    raw = load_contract_bytes(repo_root, base_sha)
    source_sha = sha256_bytes(raw)
    contract = parse_contract(raw)

    state_ids = state_ids_from_contract(contract)
    if len(state_ids) != 63:
        raise ExportError(f"expected 63 states, got {len(state_ids)}")

    transition_total = count_transitions(contract)
    if transition_total != 392:
        raise ExportError(f"expected 392 transitions, got {transition_total}")

    rows = classify_registry_rows(contract, base_sha, repo_root)
    if len(rows) != 392:
        raise ExportError(f"registry row count {len(rows)} != 392")

    transition_ids = sorted({row.transition_id for row in rows})
    if len(transition_ids) != 392:
        raise ExportError("duplicate transition_id in generator output")

    jsonl_path = target_dir / JSONL_FILENAME
    with jsonl_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            line = json.dumps(row.to_json_dict(), ensure_ascii=False, sort_keys=True)
            handle.write(line + "\n")

    manifest = {
        "schema_version": 1,
        "change_id": "consultation-funnel-baseline",
        "base_sha": base_sha,
        "source_git_path": CONTRACT_GIT_PATH,
        "source_sha256": source_sha,
        "state_ids": state_ids,
        "state_count": len(state_ids),
        "transition_ids": transition_ids,
        "transition_count": len(transition_ids),
        "generator": "tools/openspec/consultation_baseline/export_transition_registry.py",
        "generated_at": datetime.now(UTC).isoformat(),
    }
    manifest_path = target_dir / MANIFEST_FILENAME
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _ = write_classification_summary(rows, source_sha, base_sha, target_dir)
    return jsonl_path, manifest_path


def run_internal_consistency(
    repo_root: Path,
    jsonl_path: Path,
    manifest_path: Path,
) -> str:
    """Проверка 1.6: bijection и set equality manifest vs jsonl vs re-scan.

    Returns:
        Текст отчёта.

    Raises:
        ExportError: при несоответствии.
    """

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw = load_contract_bytes(repo_root, manifest["base_sha"])
    contract = parse_contract(raw)

    rows = list(iter_registry_rows(contract, manifest["base_sha"]))
    regen_ids = sorted({r.transition_id for r in rows})
    manifest_ids = sorted(manifest["transition_ids"])

    lines: list[str] = []
    lines.append("registry-internal-consistency-report")
    lines.append(f"base_sha: {manifest['base_sha']}")
    lines.append(f"source_sha256: {manifest['source_sha256']}")
    lines.append("")

    if regen_ids != manifest_ids:
        missing = set(manifest_ids) - set(regen_ids)
        extra = set(regen_ids) - set(manifest_ids)
        raise ExportError(
            f"manifest transition_ids != regenerated set; missing={len(missing)} extra={len(extra)}"
        )
    lines.append("check: regenerated_ids == manifest.transition_ids PASS")

    seen: dict[str, int] = {}
    with jsonl_path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            obj = json.loads(stripped)
            tid = obj["transition_id"]
            if tid in seen:
                raise ExportError(f"duplicate transition_id {tid} at lines {seen[tid]} and {line_no}")
            seen[tid] = line_no

    if len(seen) != 392:
        raise ExportError(f"jsonl line count {len(seen)} != 392")

    if sorted(seen.keys()) != manifest_ids:
        raise ExportError("jsonl transition_id set != manifest.transition_ids")

    lines.append("check: jsonl bijection transition_id <-> line PASS (392 rows)")
    lines.append("INTERNAL_CONSISTENCY_RESULT: PASS")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """CLI: export + internal consistency report."""

    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Корень репозитория (по умолчанию авто)",
    )
    _ = parser.add_argument("--base-sha", default=BASE_SHA)
    _ = parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=None,
        help="Override каталог evidence",
    )
    _ = parser.add_argument(
        "--skip-internal-report",
        action="store_true",
        help="Не писать registry-internal-consistency-report.txt",
    )
    args = parser.parse_args(argv)

    root = args.repo_root if args.repo_root is not None else repo_root_from_here()
    try:
        jsonl_path, manifest_path = export_registry(
            root, base_sha=args.base_sha, out_dir=args.evidence_dir
        )
        report_path = (args.evidence_dir or evidence_dir(root)) / (
            "registry-internal-consistency-report.txt"
        )
        if not args.skip_internal_report:
            report = run_internal_consistency(root, jsonl_path, manifest_path)
            report_path.write_text(report, encoding="utf-8")
        print(json.dumps({"jsonl": str(jsonl_path), "manifest": str(manifest_path)}, indent=2))
    except (ExportError, OSError, ValueError) as exc:
        print(f"EXPORT_FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
