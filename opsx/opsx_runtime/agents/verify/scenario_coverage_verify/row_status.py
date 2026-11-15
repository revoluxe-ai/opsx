"""Read-only CLI for independently stored scenario coverage observations.

No shared mutable registry and no SDK calls. The aggregate report is computed
in memory from 1 file per scenario and source-bound positive receipts.
Use: python -m agents.verify.scenario_coverage_verify.row_status
     --repo <repo> --change <change_id> [--json] [--scenario-id <id>]
"""
from __future__ import annotations

from opsx.project_context import resource_path

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from agents.common.scenario_files import plan_receipt_machine_path, read_scenario_ids
from agents.verify.scenario_coverage_verify.delta_rows import impact_scope
from agents.verify.scenario_coverage_verify.evidence_contract import (
    input_snapshot, scenario_verifier_contract_digest, source_paths, trace_mappings,
)
from agents.verify.scenario_coverage_verify.row_receipts import (
    read_observation, read_reusable_terminal_row, read_supported_row,
)
from agents.verify.scenario_coverage_verify.legacy_reuse import (
    prepare_legacy_reuse, read_legacy_terminal,
)

def inspect_rows(
    root: Path,
    change_id: str,
    *,
    model: Mapping[str, Any],
) -> list[dict[str, str]]:
    """Produce a fresh, deterministic status view, without writing anything."""
    root = root.resolve()
    plan = resource_path(root, "openspec", "changes", change_id, "scenario-coverage")
    receipt_path = plan_receipt_machine_path(plan)
    plan_id = json.loads(receipt_path.read_text(encoding="utf-8"))["PLAN_EVIDENCE_ID"]
    ids = read_scenario_ids(plan, allow_historical=True)
    mapping = trace_mappings(root, change_id)
    if not ids or len(ids) != len(set(ids)) or set(ids) != set(mapping):
        raise ValueError("SCENARIO_STATUS_PLAN_OR_MAPPING_INVALID")
    snapshot = input_snapshot(root, change_id)
    scope = impact_scope(
        root, source_paths(root, change_id), mapping,
        authority={
            "model": dict(model), "plan_evidence_id": plan_id,
            "verifier_contract_digest": scenario_verifier_contract_digest(root, model),
        },
    )
    # Read-only migration view uses the same acceptance contract as OPSX
    # scheduling. A verified historical outcome must not display as missing.
    historic = prepare_legacy_reuse(
        root, change_id=change_id, mapping=mapping,
        plan_id=plan_id, model=model,
    )
    records: list[dict[str, str]] = []
    for sid in ids:
        entry = {
            "scenario_id": sid, "result": "MISSING",
            "assessment": "", "problem": "", "origin_execution_id": "",
        }
        # The only positive authority: independently validated reusable receipt.
        pass_receipt = read_supported_row(
            root, change_id=change_id, scenario_id=sid,
            mapping=mapping, row_digest=scope["row_digests"].get(sid),
            plan_evidence_id=plan_id,
        )
        negative = read_reusable_terminal_row(
            root, change_id=change_id, scenario_id=sid,
            expected_node=mapping[sid],
            row_digest=scope["row_digests"].get(sid),
            shared_digest=scope["shared_digest"],
            source_digest=snapshot["source_digest"],
            plan_evidence_id=plan_id,
        )
        legacy = (
            read_legacy_terminal(
                root, change_id=change_id, scenario_id=sid,
                mapping=mapping, plan_id=plan_id, model=model,
                prepared=historic,
            )
            if historic is not None and pass_receipt is None and negative is None
            else None
        )
        if pass_receipt is not None and negative is not None:
            entry["result"] = "CONFLICT"
            entry["problem"] = "Conflicting positive and negative evidence for the same input"
        elif pass_receipt is not None:
            row, provenance = pass_receipt
            entry.update(
                result="PASS", assessment="SUPPORTED",
                origin_execution_id=provenance["origin_execution_id"],
                problem="",
            )
        elif negative is not None:
            row, gaps, block_reason, provenance = negative
            obs = read_observation(root, change_id=change_id, scenario_id=sid)
            entry.update(
                result="FAIL" if row.assessment == "GAP" else "PAUSED",
                assessment=row.assessment,
                origin_execution_id=provenance["origin_execution_id"],
                problem=str((obs or {}).get("problem") or block_reason or row.evidence),
                gap_class=str((obs or {}).get("gap_class") or ""),
            )
        elif legacy is not None:
            row, gaps, block_reason, provenance = legacy
            obs = read_observation(root, change_id=change_id, scenario_id=sid)
            entry.update(
                result=("PASS" if row.assessment == "SUPPORTED"
                        else "FAIL" if row.assessment == "GAP" else "PAUSED"),
                assessment=row.assessment,
                origin_execution_id=provenance["origin_execution_id"],
                problem=(
                    str((obs or {}).get("problem") or block_reason or "")
                    if row.assessment != "SUPPORTED" else ""
                ),
                gap_class=str((obs or {}).get("gap_class") or ""),
                reuse_route="LEGACY_REATTESTED",
            )
        else:
            obs = read_observation(root, change_id=change_id, scenario_id=sid)
            if obs is not None:
                entry["origin_execution_id"] = str(obs.get("origin_execution_id") or "")
                entry["assessment"] = str(obs.get("assessment") or "")
                entry["problem"] = str(obs.get("problem") or obs.get("row", {}).get("evidence") or "")
                entry["gap_class"] = str(obs.get("gap_class") or "")
                if obs.get("source_digest") != snapshot["source_digest"]:
                    entry["result"] = "STALE"
                else:
                    # A checksum-valid observation is not enough for reuse:
                    # missing scope/model/proof identity remains unverified.
                    entry["result"] = "UNVERIFIED"
        records.append(entry)
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only per-scenario OPSX receipt status")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--change", required=True)
    parser.add_argument("--scenario-id")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--model-json",
        help="Exact model selection JSON from execution; defaults to runtime preset",
    )
    args = parser.parse_args()
    if args.model_json:
        model = json.loads(args.model_json)
    else:
        from workflows.repair.repair_adapter_port import default_hop_model_selection
        model = default_hop_model_selection()
    records = inspect_rows(args.repo, args.change, model=model)
    if args.scenario_id:
        records = [r for r in records if r["scenario_id"] == args.scenario_id]
        if not records:
            raise SystemExit(f"SCENARIO_NOT_FOUND: {args.scenario_id}")
    if args.json:
        print(json.dumps(
            {"rows": records, "counts": dict(Counter(r["result"] for r in records))},
            indent=2, ensure_ascii=False,
        ))
    else:
        for row in records:
            reason = " ".join(str(row.get("problem") or "").split())[:160]
            print(f'{row["result"]:<11} {row["scenario_id"]} {reason}')
        print("TOTAL", len(records), json.dumps(
            dict(Counter(r["result"] for r in records)), ensure_ascii=False,
        ))


if __name__ == "__main__":
    main()
