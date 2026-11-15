"""One-node Bugbot smoke test in an isolated, disposable git fixture.

Run:
  python opsx/opsx_runtime/run_one_node.py --live

Outputs to opsx/runs/bugbot/smoke-<id>/, not to the real worktree.
"""

from __future__ import annotations

from opsx.project_context import resource_path, project_root

import argparse
import asyncio
import json
import subprocess
import sys
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RUNTIME_ROOT = Path(__file__).resolve().parent
REPO_ROOT = project_root()
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from agents.adapters.bridge_runtime_port import open_bridge_runtime  # noqa: E402
from agents.adapters.provider_bootstrap import ensure_adapter_factories_registered  # noqa: E402
from agents.adapters.provider_registry import build_review_adapter  # noqa: E402
from agents.verify.bugbot.agent import BugBotAgent  # noqa: E402
from workflows.verify.bugbot.bugbot_pipeline.bugbot_graph import (  # noqa: E402
    build_bugbot_graph,
)

HAIKU_LOW = {
    "id": "claude-haiku-5-5",
    "params": [
        {"id": "thinking", "value": "true"},
        {"id": "context", "value": "300k"},
        {"id": "reasoning_effort", "value": "low"},
    ],
}


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        encoding="utf-8",
        errors="replace",
    )
    return proc.stdout


def create_fixture(where: Path) -> str:
    """Build an isolated stage -> feature diff with a reproducible regression."""
    where.mkdir(parents=True, exist_ok=False)
    _git(where, "init", "-q", "-b", "stage")
    _git(where, "config", "user.name", "OPSX smoke test")
    _git(where, "config", "user.email", "opsx-smoke@example.invalid")
    policy = where / "admission.py"
    policy.write_text(
        "def eligible(age: int) -> bool:\n"
        "    return age >= 18\n",
        encoding="utf-8",
    )
    (where / "test_admission.py").write_text(
        "from admission import eligible\n\n"
        "def test_adult():\n"
        "    assert eligible(20) is True\n\n"
        "def test_child():\n"
        "    assert eligible(10) is False\n",
        encoding="utf-8",
    )
    _git(where, "add", ".")
    _git(where, "commit", "-q", "-m", "correct admission")
    _git(where, "switch", "-q", "-c", "feature/bugbot-smoke")
    policy.write_text(
        "def eligible(age: int) -> bool:\n"
        "    return age < 18\n",
        encoding="utf-8",
    )
    _git(where, "add", ".")
    _git(where, "commit", "-q", "-m", "introduce admission regression")
    return _git(where, "diff", "--no-ext-diff", "stage...HEAD")


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


async def run_one_node(*, live: bool, timeout: float) -> dict[str, Any]:
    ensure_adapter_factories_registered()
    output_dir = resource_path(REPO_ROOT, "opsx", "runs", "bugbot")
    run_dir = output_dir / f"smoke-{uuid.uuid4().hex[:10]}"
    run_dir.mkdir(parents=True, exist_ok=False)
    fixture = run_dir / "fixture"
    diff = create_fixture(fixture)
    execution_path = run_dir / "execution.json"
    report_path = run_dir / "review.json"
    adapter = build_review_adapter(timeout_seconds=timeout)
    metadata: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "scope": "isolated git fixture; NOT the stroy-system working tree",
        "fixture": str(fixture),
        "model": HAIKU_LOW,
        "execution_status": "NOT_STARTED",
        "usage": None,
        "cost": None,
        "cost_status": "not_reported",
        "cost_source": "provider agent.get_usage()",
        "usage_source": "unavailable",
        "review_path": str(report_path),
    }
    _write_json(execution_path, metadata)
    print("ARTIFACTS:", run_dir, flush=True)
    if not live:
        metadata["execution_status"] = "DRY_RUN"
        _write_json(execution_path, metadata)
        return metadata

    state = {
        "repository_path": str(fixture),
        "workspace": str(fixture),
        "model": HAIKU_LOW,
        "diff": diff,
    }
    metadata["execution_status"] = "RUNNING"
    _write_json(execution_path, metadata)
    try:
        async with open_bridge_runtime(fixture) as bridge:
            adapter = build_review_adapter(
                timeout_seconds=timeout, bridge_runtime=bridge,
            )
            graph = build_bugbot_graph(BugBotAgent(adapter))
            result = await graph.ainvoke(state)
        _write_json(report_path, result["review"])
        metadata.update(
            {
                "execution_status": result["execution_status"],
                "validation_status": "VALIDATED_JSON",
                "agent_id": result["agent_id"],
                "run_id": result["run_id"],
                "usage": result["usage"],
                "usage_source": result["usage_source"],
                "cost": result["cost"],
                "cost_status": result["cost_status"],
                "billing_error": result["billing_error"],
                "review_status": result["review"]["status"],
                "findings_count": len(result["review"]["findings"]),
            }
        )
    except Exception as exc:
        metadata.update(
            {
                "execution_status": "FAILED",
                "validation_status": "NOT_VALIDATED",
                "error": str(exc),
                "error_type": type(exc).__name__,
                "traceback": traceback.format_exc(),
            }
        )
        if adapter.last_result is not None:
            observed = adapter.last_result
            metadata.update(
                {
                    "agent_id": observed.agent_id,
                    "run_id": observed.run_id,
                    "sdk_status": observed.status,
                    "usage": observed.usage,
                    "usage_source": observed.usage_source,
                    "cost": observed.cost,
                    "cost_status": observed.cost_status,
                    "billing_error": observed.billing_error,
                }
            )
            (run_dir / "raw-response.txt").write_text(
                observed.text, encoding="utf-8"
            )
    finally:
        metadata["finished_at"] = datetime.now(timezone.utc).isoformat()
        _write_json(execution_path, metadata)
        print(
            "EXECUTION_SUMMARY:",
            json.dumps(
                {
                    key: metadata.get(key)
                    for key in (
                        "execution_status",
                        "validation_status",
                        "review_status",
                        "findings_count",
                        "usage",
                        "usage_source",
                        "cost",
                        "cost_status",
                        "error",
                        "agent_id",
                        "run_id",
                    )
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--timeout", type=float, default=150)
    args = parser.parse_args()
    result = asyncio.run(run_one_node(live=args.live, timeout=args.timeout))
    if result["execution_status"].upper() not in (
        "COMPLETED", "SUCCEEDED", "SUCCESS", "FINISHED", "DRY_RUN"
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
