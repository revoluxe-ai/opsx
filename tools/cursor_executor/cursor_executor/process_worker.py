"""Child-process entrypoint for one CursorExecutor operation."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from .executor import CursorExecutor
from .ledger import LedgerStore, atomic_write_json
from .schemas import CursorExecutionRequest


async def _run(envelope: dict[str, Any]) -> dict[str, Any]:
    ledger = LedgerStore(root=Path(envelope["ledger_root"]))
    executor = CursorExecutor(ledger=ledger)
    operation = str(envelope["operation"])
    request_raw = envelope.get("request")
    request = (
        CursorExecutionRequest.from_json(request_raw)
        if isinstance(request_raw, dict)
        else None
    )

    if operation == "execute":
        if request is None:
            raise ValueError("execute requires request")
        result = await executor.execute(request)
        return {"kind": "result", "result": result.to_json()}

    if operation == "execute_existing":
        if request is None:
            raise ValueError("execute_existing requires request")
        agent_id = str(envelope["agent_id"])
        result = await executor.execute_on_existing_agent(request, agent_id=agent_id)
        return {"kind": "result", "result": result.to_json()}
    if operation == "reconcile":
        execution_id = str(envelope["execution_id"])
        result = await executor.reconcile(execution_id, request=request)
        return {"kind": "result", "result": result.to_json()}

    if operation == "bootstrap_local_agent":
        if request is None:
            raise ValueError("bootstrap_local_agent requires request")
        execution_id = str(envelope["execution_id"])
        outcome = await executor.bootstrap_local_agent(request, execution_id=execution_id)
        return {"kind": "result", "bootstrap": outcome.to_json()}

    raise ValueError(f"unsupported operation: {operation}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--envelope", required=True)
    parser.add_argument("--response", required=True)
    args = parser.parse_args(argv)

    envelope_path = Path(args.envelope)
    response_path = Path(args.response)
    try:
        envelope = json.loads(envelope_path.read_text(encoding="utf-8"))
        payload = asyncio.run(_run(envelope))
        atomic_write_json(response_path, payload)
        return 0
    except Exception as exc:
        payload = {
            "kind": "error",
            "error": {"class": type(exc).__name__, "message": str(exc)},
        }
        atomic_write_json(response_path, payload)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
