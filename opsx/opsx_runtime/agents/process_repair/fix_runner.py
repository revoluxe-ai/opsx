"""ProcessFixAgent: META repair hop для супервизора workflow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents.process_repair.meta_surface import format_meta_surface_prompt
from agents.process_repair.schemas import ProcessFixReport
from agents.process_repair.supervisor_tool_policy import (
    SUPERVISOR_FIX_TOOLS,
    assert_supervisor_agent_tools,
)
from runtime_bootstrap import ensure_adapter_factories_registered

# ``run_pytest`` — вход в адаптер: для этого agent_key он ставит MCP ``run_opsx_pytest``.
# Имя ``run_opsx_pytest`` в списке builtin адаптер отвергает до старта модели.
def _emit_fix_line(text: str) -> None:
    """Строка супервизора в общий stdout, который сайт читает как лог агентов."""
    print(f"supervisor: {text}", flush=True)


async def run_process_supervisor_fix(request: dict[str, Any]) -> dict[str, Any]:
    """Запуск fix-hop; возвращает сериализованный ``ProcessFixReport``."""
    classification = request.get("classification") or {}
    owner = str(classification.get("owner") or "").upper()
    if owner not in {"META", "HANG"}:
        report = ProcessFixReport(
            verdict="NOT_META",
            root_cause=str(classification.get("message") or "not a META defect"),
            owner_surface=owner or "UNKNOWN",
        )
        return report.model_dump()
    project_root = Path(request["project_root"]).resolve()
    core_root = Path(request["core_root"]).resolve()
    attempt_dir = Path(request["attempt_dir"])
    attempt_dir.mkdir(parents=True, exist_ok=True)
    # Validate the finite, registered tool set before constructing an LLM agent.
    # Any shell/exec/terminal capability is a contract violation, never fallback.
    assert_supervisor_agent_tools(SUPERVISOR_FIX_TOOLS)
    ensure_adapter_factories_registered()
    import importlib.util

    loader_path = core_root / "opsx/contract_prompts/process_supervisor/loader.py"
    loader_spec = importlib.util.spec_from_file_location(
        "opsx_process_supervisor_loader", loader_path
    )
    if loader_spec is None or loader_spec.loader is None:
        base_prompt = ""
    else:
        loader_mod = importlib.util.module_from_spec(loader_spec)
        loader_spec.loader.exec_module(loader_mod)
        base_prompt = loader_mod.load_process_supervisor_fix_contract(core_root)
    skill_path = project_root / "opsx/opsx.mdc"
    if skill_path.is_file():
        skill_text = skill_path.read_text(encoding="utf-8")
    else:
        skill_text = "(скил OPSX не найден: opsx/opsx.mdc)"
    tail = str(request.get("log_tail") or "")[-12000:]
    prior = request.get("prior_dead_ends") or []
    prior_block = json.dumps(prior, ensure_ascii=False, indent=2) if prior else "[]"
    user_prompt = (
        f"{format_meta_surface_prompt()}\n\n"
        f"{base_prompt}\n\n"
        "## Скил OPSX\n"
        "Полный текст `opsx/opsx.mdc`. Это закон процесса; "
        "инструкции выше говорят, что из него можно чинить.\n\n"
        f"{skill_text}\n\n"
        "## Prior supervisor journals\n"
        "These are earlier STOP/FAIL/rejected-fix rows for this change, including "
        "rounds of the current run. Do not repeat a path already recorded here "
        "(same code, same missing artifact, same gate, same touched files). "
        "Pick a different process cause. The rows below are the journal; "
        "the NDJSON file itself is under `opsx/runs/**` and is not readable.\n"
        f"{prior_block}\n\n"
        "## Evidence\n"
        "Полный лог лежит в `opsx/runs/**`, инструмент read туда не пускает. "
        "Хвост ниже — вся улика этого раунда.\n\n"
        "## Failure classification\n"
        f"{json.dumps(classification, ensure_ascii=False, indent=2)}\n\n"
        "## Log tail\n"
        f"{tail}\n"
    )
    (attempt_dir / "fix_prompt.txt").write_text(user_prompt, encoding="utf-8")
    try:
        from agents.adapters.bridge_runtime_port import open_bridge_runtime
        from agents.adapters.hop_model_selection import supervisor_fix_model_selection
        from workflows.repair.repair_adapter_port import RepairAdapter

        timeout = int(float(request.get("timeout_seconds") or 600))
        async with open_bridge_runtime(project_root, store_backend="sqlite", store_scope="supervisor_fix") as bridge:
            adapter = RepairAdapter(bridge_runtime=bridge, timeout_seconds=timeout)
            result = await adapter.run(
                {
                    "repository_path": str(project_root),
                    "change_id": str(request.get("change_id") or "supervisor"),
                    "model": supervisor_fix_model_selection(),
                    "stream_label": "supervisor",
                },
                prompt=user_prompt,
                tools=list(SUPERVISOR_FIX_TOOLS),
                payload_model=ProcessFixReport,
                agent_key="PROCESS_SUPERVISOR_FIX",
            )
        if result.parsed is not None:
            report = result.parsed
            if isinstance(report, ProcessFixReport):
                dumped = report.model_dump()
                _emit_fix_line(
                    f"вердикт {dumped.get('verdict')}: {dumped.get('root_cause') or dumped.get('error') or ''}"
                )
                return dumped
        text = (result.text or "").strip()
        if text:
            try:
                payload = json.loads(text)
                report = ProcessFixReport.model_validate(payload)
                dumped = report.model_dump()
                _emit_fix_line(
                    f"вердикт {dumped.get('verdict')}: {dumped.get('root_cause') or dumped.get('error') or ''}"
                )
                return dumped
            except (json.JSONDecodeError, ValueError):
                pass
        report = ProcessFixReport(
            verdict="CANNOT_FIX",
            root_cause="adapter returned no valid ProcessFixReport",
            error=getattr(result, "error", None) or "parse failed",
        )
        dumped = report.model_dump()
        _emit_fix_line(f"вердикт {dumped['verdict']}: {dumped.get('error')}")
        return dumped
    except Exception as exc:
        report = ProcessFixReport(
            verdict="CANNOT_FIX",
            root_cause=str(classification.get("message") or "META defect"),
            error=f"{type(exc).__name__}: {exc}",
        )
        dumped = report.model_dump()
        _emit_fix_line(f"вердикт {dumped['verdict']}: {dumped.get('error')}")
        return dumped