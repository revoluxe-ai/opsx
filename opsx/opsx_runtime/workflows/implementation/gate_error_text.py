"""Текст gate-ошибки, по которому следующий hop любого change чинит сдачу сам.

Сообщение, равное коду (``INVALID_ARGS: INVALID_ARGS``), не называет аргумент
и не говорит, чем его заполнить. Модуль только формулирует отказ. Он не
принимает невалидный артефакт и не ослабляет gate.
"""

from __future__ import annotations

from typing import Any, Mapping

from agents.agent_visible_text import strip_slash_commands_from_text

_INVALID_ARGS_FILL = (
    "An argument is missing or has the wrong type. "
    "submit_apply_design requires content (a non-empty JSON string or object) "
    "and base_digest (the revision digest printed in the hop prompt, or an empty "
    "string when no APPLY-DESIGN file exists yet). Keys beside $from_path inside "
    "content are deep-merged onto the canonical file; do not send artifact_sha256. "
    "read_source requires source_id; when cursor is greater than 0 also pass "
    "expected_revision copied from source_revision of the first page. "
    "search_repository requires a non-empty query and a non-empty domains array. "
    "Put the invalid argument name in field."
)

_FILL_BY_CODE: dict[str, str] = {
    "INVALID_ARGS": _INVALID_ARGS_FILL,
    "DESIGN_NOT_SUBMITTED": (
        "No accepted submit_apply_design landed in this hop. Call "
        "submit_apply_design with content and base_digest before the typed hop answer."
    ),
    "DESIGN_INCOMPLETE": (
        "APPLY_DESIGN_READY is missing required fields. Include write_boundary, "
        "artifact_identity with human_path and machine_path only, next_stage set "
        "exactly to APPLY_DESIGN_REVIEW, protected_context_identity, "
        "reuse_decisions, and composition_completeness. Do not set artifact_sha256."
    ),
    "RED_EVIDENCE_NOT_PASS": (
        "Runtime replay of frozen RED test_commands did not all exit 0. "
        "A model APPLY_COMPLETE is not evidence. Fix production inside this "
        "slice's write surfaces, then rerun each frozen tests/ node with "
        "run_pytest until exit 0. Do not edit the frozen tests or write GREEN.json."
    ),
    "GREEN_VERIFICATION_MISSING": (
        "Runtime could not replay frozen RED test_commands. Each command must "
        "be a bounded poetry pytest node under tests/. Rerun those nodes with "
        "run_pytest. Do not write GREEN.json."
    ),
}


def concrete_gate_message(
    code: str,
    message: str = "",
    *,
    field: str = "",
    missing_fields: list[Any] | None = None,
) -> str:
    """Вернуть текст, который не повторяет код и называет, что заполнить.

    Уже полезное сообщение (отличное от кода) сохраняется. Пустое сообщение
    или сообщение, равное коду, заменяется общей инструкцией этого кода.
    """
    code_text = (code or "").strip() or "?"
    raw = (message or "").strip()
    useful = raw if raw and raw != code_text else ""
    if not useful:
        useful = _FILL_BY_CODE.get(code_text, "")
    if not useful and raw == code_text and code_text != "?":
        useful = (
            f"The message repeated {code_text}. Name the missing field and the "
            "value that field must hold. Do not resubmit the code as the message."
        )
    field_text = (field or "").strip()
    if field_text and f"field={field_text}" not in useful:
        useful = f"field={field_text}. {useful}" if useful else f"field={field_text}"
    if missing_fields:
        names = ", ".join(
            str(item).strip() for item in missing_fields if str(item).strip()
        )
        if names and names not in useful:
            prefix = f"missing_fields={names}."
            useful = f"{prefix} {useful}" if useful else prefix
    return useful


def format_gate_error_line(entry: Mapping[str, Any]) -> str:
    """Одна строка ``code: message`` для CLI, prompt и checkpoint."""
    code = str(entry.get("code") or "").strip() or "?"
    raw_fields = entry.get("missing_fields")
    missing = raw_fields if isinstance(raw_fields, list) else None
    message = concrete_gate_message(
        code,
        str(entry.get("message") or ""),
        field=str(entry.get("field") or ""),
        missing_fields=missing,
    )
    if message:
        return strip_slash_commands_from_text(f"{code}: {message}")
    return code


def expand_tautological_error_text(text: str) -> str:
    """Развернуть ``CODE: CODE`` и голый код из каталога. Иной текст не менять."""
    raw = text.strip()
    if not raw:
        return ""
    if ": " in raw:
        code, message = raw.split(": ", 1)
        code = code.strip()
        if code and message.strip() == code:
            return format_gate_error_line({"code": code, "message": message})
        return raw
    if raw in _FILL_BY_CODE:
        return format_gate_error_line({"code": raw, "message": raw})
    return raw
