"""Общие хелперы machine-gate после SDK hop (без shell модели)."""

from __future__ import annotations

from typing import Any

from agents.adapters.abc_adapter import TERMINAL_SDK_STATUSES

MACHINE_REPAIR_LIMIT = 1


def format_machine_errors(errors: list[dict[str, Any]]) -> str:
    """Собрать errors validator JSON для corrective prompt без обрезки списка."""
    lines: list[str] = []
    for entry in errors:
        code = str(entry.get("code") or "?")
        message = str(entry.get("message") or "")
        lines.append(f"- {code}: {message}")
    return "\n".join(lines)


def sdk_terminal(outcome: Any) -> bool:
    """True, если adapter hop завершился терминальным статусом."""
    return str(getattr(outcome, "status", "") or "").upper() in TERMINAL_SDK_STATUSES


def operative_gate_dict(
    gate: Any,
    *,
    receipt_key: str,
    repo_root: Any,
) -> dict[str, Any]:
    """Сериализовать ``StageOperativeValidation`` / RED gate в patch."""
    rel = ""
    try:
        rel = str(gate.receipt_path.relative_to(repo_root))
    except (ValueError, AttributeError):
        rel = str(getattr(gate, "receipt_path", ""))
    return {
        "ok": gate.ok,
        "status": gate.status,
        "exit_code": gate.exit_code,
        "errors": gate.errors,
        receipt_key: rel.replace("\\", "/"),
    }
