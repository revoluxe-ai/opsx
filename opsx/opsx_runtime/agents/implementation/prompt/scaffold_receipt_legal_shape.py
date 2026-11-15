"""Листовой модуль: legal-поля SCAFFOLD.json для embed и prompt (без import apply_scaffold)."""

from __future__ import annotations

# Поля, которые runtime дописывает сам; модель не угадывает значения.
_RUNTIME_OWNED_FIELDS = frozenset({"work_scope_digest"})

SCAFFOLD_BLOCKED_FIELDS: tuple[str, ...] = (
    "schema (const opsx.apply_scaffold)",
    "schema_version (const 1)",
    "change_id",
    "slice_id",
    'status (const "BLOCKED")',
    "generated_at (ISO-8601 UTC)",
    "chat_history_required (const false)",
    "behavior_implemented (const false)",
    "tests_modified (const false)",
    "tests_executed (const false)",
    'blockers (array, min 1: { "code", optional "detail" })',
    'next_stage (const "NONE")',
)

SCAFFOLD_READY_FIELDS: tuple[str, ...] = (
    "schema (const opsx.apply_scaffold)",
    "schema_version (const 1)",
    "change_id",
    "slice_id",
    "apply_design_identity (copy object from embedded apply_design_json; do not invent digests)",
    'status (const "SCAFFOLD_READY")',
    "generated_at (ISO-8601 UTC)",
    "chat_history_required (const false)",
    "repository_identity_before (object: head, inspected_paths, inspected_digest)",
    "repository_identity_after (object: same shape as before)",
    "required_surfaces (array from slice scaffold_contract in apply_design_json)",
    "created_paths (repo-relative strings; empty allowed when no_op is true)",
    "extended_paths (repo-relative strings)",
    "created_symbols (strings)",
    "structural_changes (array of { path, kind, detail })",
    "no_op (boolean)",
    "no_op_reason (string, required when no_op is true)",
    "behavior_implemented (const false)",
    "product_files_modified (boolean; must be false when no_op is true)",
    "tests_modified (const false)",
    "tests_executed (const false)",
    "authorized_write_paths (repo-relative strings from design work_scope)",
    "other_eligible_slice_ids (strings)",
    "blockers (const empty array)",
    'next_stage (const "APPLY_RED")',
)


def scaffold_receipt_legal_shape_embed() -> dict[str, object]:
    """Структура для slice input pack: единственный источник legal receipt shape."""
    return {
        "runtime_owned_fields": sorted(_RUNTIME_OWNED_FIELDS),
        "blocked_branch_required": list(SCAFFOLD_BLOCKED_FIELDS),
        "ready_branch_required": list(SCAFFOLD_READY_FIELDS),
        "rules": [
            "Match exactly one branch; never mix BLOCKED and SCAFFOLD_READY keys.",
            "When no_op is false, at least one entry in created_paths or extended_paths.",
            "When no_op is true, product_files_modified must be false.",
            "Do not read workflow runtime sources or registry JSON to discover this shape.",
        ],
    }
