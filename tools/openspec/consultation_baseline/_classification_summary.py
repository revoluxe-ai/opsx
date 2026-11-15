"""Генерация ``registry-classification-summary.md`` для tasks §2.6."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from tools.openspec.consultation_baseline._registry_build import RegistryRow


def build_classification_summary_markdown(
    rows: list[RegistryRow],
    source_sha256: str,
    base_sha: str,
) -> str:
    """Собрать markdown-отчёт с counts и списком UNMAPPED."""

    primary_counts = Counter(row.runtime_path_primary for row in rows)
    multi_path = sum(1 for row in rows if len(row.runtime_paths) > 1)
    unmapped = [row for row in rows if row.runtime_path_primary == "UNMAPPED_AT_BASELINE"]

    lines: list[str] = []
    lines.append("# Registry classification summary (Stage 2)")
    lines.append("")
    lines.append(f"- **base_sha:** `{base_sha}`")
    lines.append(f"- **source_sha256:** `{source_sha256}`")
    lines.append(f"- **transition_count:** {len(rows)}")
    lines.append("")
    lines.append("## Counts by `runtime_path_primary`")
    lines.append("")
    for key in (
        "JSON_SIMPLE_CTA",
        "PYTHON_FSM_PREEMPT",
        "HANDLER_ADAPTER",
        "UNMAPPED_AT_BASELINE",
    ):
        lines.append(f"- {key}: {primary_counts.get(key, 0)}")
    lines.append(f"- **sum:** {sum(primary_counts.values())}")
    lines.append(f"- **multi-path rows** (`len(runtime_paths)>1`): {multi_path}")
    lines.append("")
    lines.append("## Evidence posture")
    lines.append("")
    lines.append(
        "Classification records static handling paths @ BASE_SHA. "
        "`evidence_status` remains **UNVERIFIED** unless separate P0/P1 "
        "tasks prove execution (D-BL-04)."
    )
    lines.append("")
    lines.append("## UNMAPPED transitions")
    lines.append("")
    if not unmapped:
        lines.append("_None._")
    else:
        for row in unmapped:
            lines.append(
                f"- `{row.transition_id}` — `{row.source_state}` / "
                f"`{row.declared_trigger}` → `{row.declared_target}`"
            )
    lines.append("")
    lines.append("## Residual gaps")
    lines.append("")
    lines.append(
        "- Composite or guard-only contract triggers may not map 1:1 to catalog "
        "button labels; rows stay UNMAPPED without invented aliases."
    )
    lines.append(
        "- Conditional FSM branches (booking guards, edit_mode) are noted in "
        "per-row `gap` text; matrix targets may differ from runtime target."
    )
    return "\n".join(lines) + "\n"


def write_classification_summary(
    rows: list[RegistryRow],
    source_sha256: str,
    base_sha: str,
    evidence_dir: Path,
) -> Path:
    """Записать ``registry-classification-summary.md`` в evidence."""

    path = evidence_dir / "registry-classification-summary.md"
    path.write_text(
        build_classification_summary_markdown(rows, source_sha256, base_sha),
        encoding="utf-8",
    )
    return path
