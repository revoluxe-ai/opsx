"""Validate a finished provider response and refresh server-reported billing.

No new agent message is sent. This command does NOT invoke a paid model run.

Usage: python opsx/opsx_runtime/reconcile_one_node.py <run-dir>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agents.adapters.abc_adapter import ABCAdapter, AgentResult  # noqa: E402
from agents.adapters.provider_bootstrap import ensure_adapter_factories_registered  # noqa: E402
from agents.adapters.reconcile_billing_port import refresh_saved_run_billing  # noqa: E402
from agents.verify.bugbot.agent import BugBotAgent  # noqa: E402
from workflows.verify.bugbot.bugbot_pipeline.bugbot_graph import (  # noqa: E402
    build_bugbot_graph,
)
from run_one_node import _git, _write_json  # noqa: E402


class ReplayAdapter(ABCAdapter):
    def __init__(self, metadata: Mapping[str, Any], raw_text: str):
        self.metadata = metadata
        self.raw_text = raw_text

    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: Any = None,
        resume_agent_id: str | None = None,
    ) -> AgentResult:
        return AgentResult(
            text=self.raw_text,
            status=str(self.metadata["sdk_status"]),
            agent_id=str(self.metadata["agent_id"]),
            run_id=str(self.metadata["run_id"]),
            usage=self.metadata.get("usage"),
            cost=self.metadata.get("cost"),
            usage_source=str(self.metadata.get("usage_source", "unavailable")),
            cost_status=str(self.metadata.get("cost_status", "pending")),
            billing_error=self.metadata.get("billing_error"),
        )


async def revalidate(metadata: dict[str, Any], run_dir: Path) -> None:
    raw_path = run_dir / "raw-response.txt"
    if not raw_path.exists():
        return
    fixture = Path(metadata["fixture"])
    diff = _git(fixture, "diff", "--no-ext-diff", "stage...HEAD")
    graph = build_bugbot_graph(
        BugBotAgent(ReplayAdapter(metadata, raw_path.read_text(encoding="utf-8")))
    )
    state = {
        "repository_path": str(fixture),
        "workspace": str(fixture),
        "model": metadata["model"],
        "diff": diff,
    }
    result = await graph.ainvoke(state)
    _write_json(run_dir / "review.json", result["review"])
    old_error = metadata.pop("error", None)
    old_traceback = metadata.pop("traceback", None)
    metadata["original_run_error"] = old_error
    metadata["original_run_traceback"] = old_traceback
    metadata["execution_status"] = result["execution_status"]
    metadata["validation_status"] = "VALIDATED_JSON_BY_OFFLINE_REPLAY"
    metadata["review_status"] = result["review"]["status"]
    metadata["findings_count"] = len(result["review"]["findings"])
    metadata["replayed_at"] = datetime.now(timezone.utc).isoformat()


async def main(run_dir: Path) -> None:
    ensure_adapter_factories_registered()
    meta_path = run_dir / "execution.json"
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    await revalidate(metadata, run_dir)
    await refresh_saved_run_billing(metadata)
    _write_json(meta_path, metadata)
    print(
        json.dumps(
            {
                "review": metadata.get("review_status"),
                "validation": metadata.get("validation_status"),
                "usage": metadata.get("usage"),
                "cost": metadata.get("cost"),
                "cost_status": metadata.get("cost_status"),
                "billing_error": metadata.get("billing_error"),
                "run_id": metadata.get("run_id"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    asyncio.run(main(args.run_dir.resolve()))
