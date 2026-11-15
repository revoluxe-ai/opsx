"""Текстовая схема SCAFFOLD.json для apply-scaffold hop (без путей к registry/runtime)."""

from __future__ import annotations

from agents.config import ReadScopeViolation
from agents.implementation.apply_scaffold.scaffold_hop_visible_text import (
    sanitize_scaffold_hop_visible_text,
)
from agents.implementation.prompt.scaffold_receipt_legal_shape import (
    SCAFFOLD_BLOCKED_FIELDS as _SCAFFOLD_BLOCKED_FIELDS,
    SCAFFOLD_READY_FIELDS as _SCAFFOLD_READY_FIELDS,
    scaffold_receipt_legal_shape_embed,
)

_RUNTIME_OWNED_FIELDS = frozenset({"work_scope_digest"})


def _bullet_block(title: str, items: tuple[str, ...]) -> str:
    lines = "\n".join(f"- {item}" for item in items)
    return f"### {title}\n{lines}"


def scaffold_receipt_authority_block() -> str:
    """Справочник legal-полей SCAFFOLD receipt (runtime пишет JSON; модель не редактирует)."""
    runtime = ", ".join(sorted(_RUNTIME_OWNED_FIELDS))
    return (
        "## SCAFFOLD receipt reference (runtime-owned on disk)\n"
        "Do **not** author or edit ``SCAFFOLD.json``. After the hop the runtime materializes "
        "``SCAFFOLD_READY`` or ``BLOCKED`` from design, git diff, and hop status.\n"
        "Hop answer statuses stay in hop JSON only — never ``SCAFFOLD_READY`` in hop ``status``.\n\n"
        f"Runtime-owned receipt fields (never set by the agent): {runtime}.\n"
        "Field lists below and ``scaffold_json_field_lists`` in the slice input pack are "
        "read-only reference — do not open registry tooling.\n\n"
        + _bullet_block("BLOCKED branch (minimal)", _SCAFFOLD_BLOCKED_FIELDS)
        + "\n\n"
        + _bullet_block("SCAFFOLD_READY branch (full)", _SCAFFOLD_READY_FIELDS)
        + "\n\n"
        "If product scaffold shape is already on disk and PRE gate was valid, prefer hop "
        "``NO_CHANGE``.\n"
    )


def scaffold_receipt_correction_block(violation: ReadScopeViolation) -> str:
    """Текст коррекции после scaffold_receipt_schema без forbidden path hints."""
    errors = sanitize_scaffold_hop_visible_text(
        str(violation.command or violation).strip(),
    )
    embed = scaffold_receipt_legal_shape_embed()
    blocked = "\n".join(f"- {f}" for f in embed["blocked_branch_required"])
    ready = "\n".join(f"- {f}" for f in embed["ready_branch_required"])
    return (
        "## SCAFFOLD.json WRITE REJECTED (machine schema)\n\n"
        "Your last edit to the slice SCAFFOLD receipt JSON was rolled back. "
        "Rewrite the file to match **exactly one** branch below. "
        "Do not mix statuses or paste a partial ready branch.\n\n"
        f"Validator errors:\n{errors}\n\n"
        "### BLOCKED branch\n"
        f"{blocked}\n\n"
        "### SCAFFOLD_READY branch\n"
        f"{ready}\n\n"
        "Runtime-owned (omit): "
        + ", ".join(str(x) for x in embed["runtime_owned_fields"])
        + ".\n"
        "Do not edit SCAFFOLD.json; return hop JSON with BLOCKED or fix product shape only."
    )
