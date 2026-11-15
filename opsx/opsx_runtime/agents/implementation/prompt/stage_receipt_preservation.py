"""Контракт preservation для slice-hop prompt (один signer — pipeline-identity)."""

from __future__ import annotations

from typing import Any, Mapping

_SLICE_CLOSING_STAGE = "apply_green"
_MID_SLICE_KEYS = frozenset({"apply_scaffold", "apply_red", "apply_implement"})

# Общий блок authority / prompt (English — как у stage authority).
STAGE_RECEIPT_PRESERVATION_AUTHORITY = """\
## Slice hop contract (mandatory)

**Receipt visible to this hop only.** Work only with stage receipts and files this
hop can read on disk (paths in authority + artifact bundle). If the slice receipt
is missing, unreadable, or not in scope for this hop: do **not** invent, delete, or
rewrite a receipt you cannot see; return `NO_CHANGE`, `BLOCKED` (Owner only), or
`ESCALATE_ARBITER` when a cross-hop gap blocks honest work.

**Valid receipt + files already there.** When `slice_receipt_validation.valid === true`
in hop context (deterministic Python gate; you do not re-run it): do **not** wipe or
rewrite receipts. The runtime refreshes fingerprints without the model. Skip with
`NO_CHANGE` when satisfied; otherwise patch **in place** only work files
(`machine_gate_errors`).

**Fingerprints.** Never set or edit `work_file_fingerprints` and never re-run the
Python slice receipt validator yourself. Runtime writes fingerprints and POST-validates
after this hop.

**Receipt JSON bodies.** Stage `*.json` under `apply-*`: author status + path lists
matching the hop answer schema. **Do not** read or run registry schema tooling; runtime
runs stage validators and refreshes fingerprints/digests after this hop.

**After APPLY DESIGN is redone**, upstream slice receipts may be stale — still **no**
delete-and-rewrite; patch bindings in place when machine gate errors say so.

**Return to APPLY DESIGN** is allowed **only** from APPLY GREEN (slice closing
stage) when the slice cannot be executed as frozen (`BLOCKED` + graph design redo).
Mid-slice hops (SCAFFOLD, RED, IMPLEMENT) must **not** edit `apply-design/**`;
use `status=ESCALATE_ARBITER` with `arbiter_request` for design/oracle conflicts.
Owner PAUSE / ADL NO_GO → `BLOCKED` only.
"""


def operative_receipt_from_plan(state: Mapping[str, Any]) -> dict[str, Any] | None:
    """Извлечь ``prior_stage_operative`` из plan payload в state."""
    plan = state.get("implementation_plan")
    if not isinstance(plan, dict):
        return None
    operative = plan.get("prior_stage_operative")
    if not isinstance(operative, dict):
        return None
    return dict(operative)


def slice_receipt_validation_dispatch_block(
    validation: Mapping[str, Any] | None,
) -> str:
    """Фрагмент prompt: готовый JSON gate (модель не перепроверяет и не пишет receipt)."""
    if not isinstance(validation, dict):
        return ""
    valid = bool(validation.get("valid"))
    code = validation.get("code")
    gate = validation.get("gate")
    paths = validation.get("work_paths")
    lines = [
        "\nDeterministic slice receipt gate (PRE-computed; do NOT re-validate or write receipts):\n",
        f"- valid: {valid}",
        f"- gate: {gate!r}",
        f"- code: {code!r}",
    ]
    if isinstance(paths, list) and paths:
        lines.append(f"- work_paths: {paths!r}")
    if valid:
        lines.append(
            "\nGate PASS: return `NO_CHANGE` unless `machine_gate_errors` require a "
            "bounded patch to work files only. Do not re-validate or touch fingerprints.\n"
        )
    else:
        lines.append(
            "\nGate FAIL: do this stage's bounded work (product/tests/green loop per "
            "authority). Update receipt JSON only via stage Node tools; runtime adds "
            "fingerprints and POST-validates after your hop.\n"
        )
    return "\n".join(lines)


def preservation_dispatch_block(operative: Mapping[str, Any] | None) -> str:
    """Фрагмент prompt при operative-valid из plan (дополняет validator на диске)."""
    if not operative or not operative.get("authorized"):
        return ""
    status = operative.get("artifact_status")
    code = operative.get("code")
    lines = [
        "\n(LEGACY super-implement plan-loop only — ignore when "
        "`slice_receipt_validation` is in hop context.)\n",
        "\nOperative receipt for THIS dispatch (plan hint; do not treat as authoritative "
        "when slice receipt gate is present):\n",
        "- authorized: true",
        f"- artifact_status: {status!r}",
    ]
    if code:
        lines.append(f"- plan code (null when fully valid): {code!r}")
    lines.append(
        "\nReceipt valid + files present → skip or patch in place only; never "
        "wipe or rewrite from scratch.\n"
    )
    return "\n".join(lines)


def mid_slice_design_ban_block(stage_key: str) -> str:
    """Запрет design-redo для SCAFFOLD/RED/IMPLEMENT hops."""
    if stage_key not in _MID_SLICE_KEYS:
        return ""
    return (
        "\nMid-slice hop: do not edit `apply-design/**`. Design/oracle gaps outside "
        "this stage → `status=ESCALATE_ARBITER` + `arbiter_request`. Owner-only "
        "pause → `BLOCKED`.\n"
    )


def green_design_redo_block(stage_key: str) -> str:
    """Единственный hop, который может инициировать design redo через graph."""
    if stage_key != _SLICE_CLOSING_STAGE:
        return ""
    return (
        "\nSlice closing stage only: if the frozen slice cannot be executed after "
        "bounded repair, you may set `status=BLOCKED` and document "
        "`planning_claim` vs `repository_fact` in `remaining_gaps` / "
        "`gap_closure_summary` so the graph can rerun APPLY DESIGN. Do not edit "
        "design files in this hop.\n"
    )
