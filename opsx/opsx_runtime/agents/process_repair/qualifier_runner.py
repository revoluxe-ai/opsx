"""Qualifier hop супервизора: только классификация, без правок файлов."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents.process_repair.schemas import ProcessQualifierVerdict


def _emit_qualify_line(text: str) -> None:
    """Строка qualifier в общий stdout супервизорного worker."""
    print(f"supervisor: qualify: {text}", flush=True)


def _load_qualifier_contract(core_root: Path) -> str:
    import importlib.util

    loader_path = core_root / "opsx/contract_prompts/process_supervisor/loader.py"
    loader_spec = importlib.util.spec_from_file_location(
        "opsx_process_supervisor_loader", loader_path
    )
    if loader_spec is None or loader_spec.loader is None:
        return ""
    loader_mod = importlib.util.module_from_spec(loader_spec)
    loader_spec.loader.exec_module(loader_mod)
    return loader_mod.load_process_supervisor_qualifier_contract(core_root)


def run_process_supervisor_qualify(request: dict[str, Any]) -> dict[str, Any]:
    """Детерминированный qualifier: owner, code, root_cause, fix_hop_allowed.

    Контракт ``QUALIFIER_AGENT.md`` задаёт правила; исполнение без LLM и без
    ``edit`` — контракт QUALIFIER_AGENT; исполнение в рантайме контроллера.
    """
    core_root = Path(request["core_root"]).resolve()
    attempt_dir = Path(request["attempt_dir"])
    attempt_dir.mkdir(parents=True, exist_ok=True)
    contract = _load_qualifier_contract(core_root)
    tail = str(request.get("log_tail") or "")[-12000:]
    record = request.get("worker_record") or {}
    hang = request.get("hang_reason")
    (attempt_dir / "qualify_contract.txt").write_text(contract, encoding="utf-8")
    (attempt_dir / "qualify_evidence.txt").write_text(
        f"## Worker record\n{json.dumps(record, ensure_ascii=False, indent=2)}\n\n"
        f"## Log tail\n{tail}\n",
        encoding="utf-8",
    )
    from opsx_cli.supervisor_qualifier import qualify_failure

    verdict = qualify_failure(record, log_tail=tail, hang_reason=hang)
    payload = ProcessQualifierVerdict(
        owner=verdict.owner,
        code=verdict.code,
        stage=verdict.stage,
        fingerprint=verdict.fingerprint,
        root_cause=verdict.root_cause,
        message=verdict.message,
        fix_hop_allowed=verdict.fix_hop_allowed,
    )
    dumped = payload.model_dump()
    _emit_qualify_line(
        f"owner={dumped['owner']} code={dumped['code']} "
        f"fix_hop_allowed={dumped['fix_hop_allowed']}"
    )
    return dumped