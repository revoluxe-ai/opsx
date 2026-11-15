"""CLI: implementation LangGraph (split-APPLY lane via super-implement plan SoT)."""

from __future__ import annotations

from opsx.project_context import resource_path, project_root

import argparse
import asyncio
from contextlib import AsyncExitStack
import sys
import uuid
from pathlib import Path
from typing import Any

RUNTIME_ROOT = Path(__file__).resolve().parent
REPO_ROOT = project_root()
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

from agents.adapters.adapter_stream_constants import CURSOR_HOP_HARD_DEADLINE_SECONDS  # noqa: E402
from agents.adapters.bridge_runtime_port import open_bridge_runtime  # noqa: E402
from agents.adapters.provider_bootstrap import ensure_adapter_factories_registered  # noqa: E402
from runtime_bootstrap import ensure_provider_wheel_importable  # noqa: E402

ensure_provider_wheel_importable()
ensure_adapter_factories_registered()
from agents.verify.impl_verify.preflight import change_root  # noqa: E402
from workflows.implementation.graph import build_implementation_hop_agents  # noqa: E402
from workflows.implementation.run import run_implementation_workflow  # noqa: E402
from workflows.repair.repair_adapter_port import RepairAdapter  # noqa: E402
from workflows.repo_paths import resolve_opsx_repository_root  # noqa: E402
from workflows.workflow_logging import ensure_workflow_stderr_logging  # noqa: E402


async def execute(
    change_id: str,
    *,
    repo_root: Path,
    timeout_seconds: float = CURSOR_HOP_HARD_DEADLINE_SECONDS,
) -> dict[str, Any]:
    """Запустить implementation lane для change."""
    ensure_workflow_stderr_logging()
    repo_root = resolve_opsx_repository_root(repo_root)
    async with AsyncExitStack() as bridge_stack:
        bridge = await bridge_stack.enter_async_context(open_bridge_runtime(repo_root))
        apply_adapter = RepairAdapter(
            timeout_seconds=timeout_seconds, bridge_runtime=bridge,
        )
        hop_agents = build_implementation_hop_agents(apply_adapter=apply_adapter)
        run_dir = resource_path(REPO_ROOT, "opsx", "runs", "implement", change_id)
        run_dir.mkdir(parents=True, exist_ok=True)
    
        initial_state: dict[str, Any] = {
            "change_id": change_id,
            "repository_path": str(repo_root),
            "live_execution_authorized": True,
        }
        final = await run_implementation_workflow(
            initial_state,
            repo_root=repo_root,
            hop_agents=hop_agents,
            run_dir=run_dir,
        )
        return dict(final)


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(description="OPSX implementation LangGraph lane")
    parser.add_argument("change_id", help="OpenSpec change id")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)
    _ = change_root(args.change_id, repo_root=args.repo_root)
    result = asyncio.run(
        execute(
            args.change_id,
            repo_root=args.repo_root,
        ),
    )
    status = str(result.get("implementation_status") or "")
    print(f"implementation_status={status}")
    if result.get("implementation_recovery_code"):
        print(f"recovery_code={result.get('implementation_recovery_code')}")
    if status == "APPLY_COMPLETE":
        return 0
    if status == "PAUSED":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
