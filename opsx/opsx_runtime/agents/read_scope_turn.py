"""Хелперы read-scope для ``ABCAgent.submit_turn`` (промпт, guard, коррекция).

Транспорт получает только ``tool_call_guard``; политику он не интерпретирует.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from agents.config import (
    READ_CLASS_TOOLS,
    WRITE_CLASS_TOOLS,
    ReadScope,
    ReadScopeViolation,
    assert_paths_in_scope_from_record,
)

READ_SCOPE_MARKER = "## READ SCOPE"

_FILESYSTEM_TOOLS = frozenset(READ_CLASS_TOOLS) | frozenset(WRITE_CLASS_TOOLS) | {"shell"}


def role_uses_filesystem_tools(tools: Sequence[str]) -> bool:
    """Нужно ли хопу дописывать каталоги в промпт."""
    return bool(_FILESYSTEM_TOOLS.intersection({str(t).strip().lower() for t in tools}))


def build_tool_call_guard(
    scope: ReadScope | None,
    repo_root: Path | None,
    *,
    agent_key: str | None = None,
) -> Callable[..., None] | None:
    """Callback для транспорта: read-scope и (apply_scaffold) post-edit schema gate."""
    if scope is None:
        return None
    snapshots: dict[str, str] = {}
    slice_receipt_gate = (
        agent_key in {"apply_scaffold", "apply_red", "apply_implement", "apply_green"}
        and repo_root is not None
    )
    product_edit_gate = (
        agent_key in {"apply_scaffold", "apply_red", "apply_implement", "apply_green"}
        and repo_root is not None
    )

    def guard(
        tool_name: str,
        args: Mapping[str, Any],
        record: Mapping[str, Any] | None = None,
    ) -> None:
        assert_paths_in_scope_from_record(
            scope,
            tool_name=tool_name,
            args=args,
            repo_root=repo_root,
        )
        if record is None or repo_root is None:
            return
        if product_edit_gate:
            from agents.implementation.product_file_edit_guard import (
                enforce_product_file_edit_guard,
            )

            enforce_product_file_edit_guard(
                tool_name,
                args,
                record,
                repo_root=repo_root,
                snapshots=snapshots,
            )
        if slice_receipt_gate:
            from agents.implementation.slice_stage_receipt_edit_guard import (
                enforce_slice_receipt_after_edit,
            )

            enforce_slice_receipt_after_edit(
                tool_name,
                args,
                record,
                repo_root=repo_root,
                snapshots=snapshots,
            )

    return guard


def _is_slice_apply_scope(scope: ReadScope) -> bool:
    """Узкий slice-apply профиль без ``opsx_tools``."""
    allow = scope.restrict_allow or ()
    if any(p.startswith("opsx_tools") for p in allow):
        return False
    return any(p.startswith("openspec/changes") for p in allow)


def _slice_apply_correction_block(scope: ReadScope, state: Mapping[str, Any]) -> str:
    """Доп. подсказки после нарушения read-scope на slice apply hop."""
    if not _is_slice_apply_scope(scope):
        return ""
    slice_id = str(state.get("implementation_slice_id") or "").strip()
    stage = str(state.get("implementation_stage") or "").strip().lower()
    lines = [
        "Slice apply (read-scope):",
        "- **Запрещено** читать или grep/glob каталог `opsx_tools` — runtime Python "
        "валидирует stage receipts и пишет digests.",
        "- Не считай sha256 вручную: без `python -c`, `node -e`, обхода identity.",
        "- Рабочие пути: `src/**`, `app/**`, `tests/**`, `openspec/changes|decisions|specs/**`, "
        "`migrations/**`, `pyproject.toml`, `poetry.lock`.",
        "- RED/GREEN: shell только для `test_commands` из slice/RED.json и bounded "
        "product edits в scope стадии.",
    ]
    if slice_id:
        lines.append(
            f"- Slice `{slice_id}`: receipts стадии можно читать в "
            "`openspec/changes/<id>/apply-*`. Не используй `git show` и не сканируй корень репо."
        )
    if "red" in stage:
        lines.append("- Не редактируй scaffold/production из RED; только tests/fixtures.")
    return "\n".join(lines) + "\n\n"


def _hop_work_paths_hint(state: Mapping[str, Any]) -> str:
    """Явные work_paths из PRE-gate (без glob от корня)."""
    raw = state.get("implementation_slice_receipt_validation")
    data: Mapping[str, Any] | None = None
    if isinstance(raw, Mapping):
        data = raw
    elif isinstance(raw, str) and raw.strip():
        import json

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, Mapping):
            data = parsed
    if data is None:
        return ""
    paths = data.get("work_paths")
    if not isinstance(paths, list) or not paths:
        return ""
    lines = "\n".join(f"- `{p}`" for p in paths if str(p).strip())
    if not lines:
        return ""
    return (
        "Используй только `read`/`grep` по work_paths (уже в hop-контексте); "
        "не вызывай `glob` без `target_directory` внутри ALLOW:\n"
        f"{lines}\n\n"
    )


def _repair_finding_path_hint(state: Mapping[str, Any]) -> str:
    """Путь из dispatcher finding для repair-hop (без скана change root)."""
    item = state.get("dispatcher_current_item")
    if not isinstance(item, Mapping):
        return ""
    for key in ("path", "file", "location"):
        raw = item.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    evidence = item.get("evidence_refs")
    if isinstance(evidence, list):
        for ref in evidence:
            if isinstance(ref, str) and ref.strip():
                return ref.strip()
            if isinstance(ref, Mapping):
                p = ref.get("path")
                if isinstance(p, str) and p.strip():
                    return p.strip()
    return ""


def read_scope_correction_prompt(
    violation: ReadScopeViolation,
    scope: ReadScope,
    state: Mapping[str, Any],
    *,
    base_prompt: str = "",
    repo_root: Path | None = None,
    non_discovery_outside_allow: bool = False,
) -> str:
    """Дописать коррекцию к исходному hop-промпту (не заменять INPUT CONTEXT).

    Args:
        non_discovery_outside_allow: Единственный resume на non-discovery hop после
            ``outside_restrict_allow``: полный список ALLOW и выход через BLOCKED/remaining_gaps.
    """
    if non_discovery_outside_allow:
        allow_sample = list(scope.restrict_allow or ())
    else:
        allow_sample = list(scope.restrict_allow or ())[:8]
    allow_lines = "\n".join(f"- `{p}`" for p in allow_sample)
    slice_block = _slice_apply_correction_block(scope, state)
    work_paths_hint = ""
    if violation.rule == "repo_root_scan":
        work_paths_hint = _hop_work_paths_hint(state)
    finding_path = _repair_finding_path_hint(state)
    work_hint = ""
    if finding_path:
        work_hint = (
            f"Используй только встроенный контекст и явный путь finding: `{finding_path}`.\n"
        )
    root_line = ""
    if repo_root is not None:
        root_line = (
            f"- REPO_ROOT (запрещён как path): `{repo_root.resolve()}`\n"
        )
    allow_heading = (
        "Разрешённые префиксы (repo-relative, только они):\n"
        if non_discovery_outside_allow
        else "Примеры разрешённых префиксов:\n"
    )
    exit_block = ""
    if non_discovery_outside_allow:
        exit_block = (
            "Если задачу нельзя выполнить только правками внутри ALLOW выше, "
            "верни финальный JSON hop по существующему контракту: "
            "статус BLOCKED и/или поле remaining_gaps — "
            "не выбирай другие файлы и не вызывай edit/write/delete/read вне ALLOW.\n\n"
        )
    correction = (
        "## READ SCOPE CORRECTION\n\n"
        "Предыдущий tool-call отклонён read-scope gate.\n\n"
        f"- tool: `{violation.tool}`\n"
        f"- path: `{violation.path}`\n"
        f"- rule: `{violation.rule}`\n"
        f"{root_line}\n"
        "ЖЁСТКО: filesystem tools только по ALLOW исходного блока READ SCOPE. "
        "Запрещены grep/glob/read от **корня workspace**, родителя `openspec/` "
        "и любого пути вне ALLOW. "
        "Контекст уже встроен в исходный промпт ниже — не ищи его обходом дерева.\n\n"
        f"{slice_block}"
        f"{work_paths_hint}"
        f"{work_hint}"
        f"{allow_heading}"
        f"{allow_lines}\n\n"
        f"{exit_block}"
        "Продолжи задачу hop без повторения нарушения."
    )
    base = (base_prompt or "").strip()
    if not base:
        return correction
    return f"{base}\n\n{correction}"


def enrich_violation(
    exc: ReadScopeViolation,
    *,
    agent_id: str | None,
    run_id: str | None,
) -> ReadScopeViolation:
    """Дополнить нарушение id сессии для resume в ``submit_turn``."""
    if exc.agent_id and exc.run_id:
        return exc
    return ReadScopeViolation(
        tool=exc.tool,
        path=exc.path,
        rule=exc.rule,
        command=exc.command,
        pattern_hint=getattr(exc, "pattern_hint", "") or exc.rule,
        agent_id=str(agent_id or exc.agent_id or ""),
        run_id=str(run_id or exc.run_id or ""),
    )
