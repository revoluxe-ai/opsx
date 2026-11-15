"""Runtime handler custom tool ``record_slice_receipt`` (все slice receipt hops)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from agents.adapters.agent_capability_options import AgentCustomToolSpec
from agents.implementation.slice_receipt_tool_schema import RECORD_SLICE_RECEIPT_SCHEMA
from workflows.implementation.stage_receipt_writer import StageReceiptWriter


class SliceReceiptToolRuntime:
    """Замыкание change + repo для ``record_slice_receipt``."""

    def __init__(self, *, change_id: str, repo_root: Path) -> None:
        self._change_id = change_id.strip()
        self._repo_root = repo_root.resolve()
        self.last_result: dict[str, Any] | None = None

    async def record_slice_receipt(
        self,
        args: Mapping[str, Any],
        _ctx: Any,
    ) -> dict[str, Any]:
        """Принять минимальное hop-решение и записать schema-valid receipt on-disk."""
        if not isinstance(args, Mapping):
            self.last_result = {"ok": False, "code": "INVALID_ARGS"}
            return dict(self.last_result)
        known = set(RECORD_SLICE_RECEIPT_SCHEMA["properties"])
        unknown = set(args.keys()) - known
        if unknown:
            self.last_result = {
                "ok": False,
                "code": "UNKNOWN_ARGS",
                "fields": sorted(unknown),
            }
            return dict(self.last_result)
        result = StageReceiptWriter.record_hop_decision(
            self._change_id,
            repo_root=self._repo_root,
            decision=args,
        )
        if result.ok:
            self.last_result = {
                "ok": True,
                "stage": result.stage,
                "path": result.path.as_posix() if result.path else None,
            }
        else:
            self.last_result = {
                "ok": False,
                "code": result.code,
                "stage": result.stage,
            }
        return dict(self.last_result)


def attach_slice_receipt_tool_transport(
    transport: dict[str, Any],
    *,
    change_id: str,
    repo_root: Path,
) -> dict[str, Any]:
    """Добавить ``slice_receipt_tool_runtime`` в adapter transport state."""
    out = dict(transport)
    out["slice_receipt_tool_runtime"] = SliceReceiptToolRuntime(
        change_id=change_id,
        repo_root=repo_root,
    )
    return out


def build_slice_receipt_custom_tools(
    runtime: SliceReceiptToolRuntime,
) -> dict[str, AgentCustomToolSpec]:
    """Custom tools для merge в model adapter hop (receipt пишет runtime, tool не экспонируется)."""
    _ = runtime
    _ = RECORD_SLICE_RECEIPT_SCHEMA
    return {}
