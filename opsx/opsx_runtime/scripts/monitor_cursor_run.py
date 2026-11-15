"""Опрос GetRun и метрик live.ndjson для обнаружения «тихой» остановки Cursor run.

Не останавливает и не перезапускает агент; только логирует статус сервера и рост NDJSON.
"""

from __future__ import annotations

from opsx.project_context import resource_path, project_root

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = project_root()
RUNTIME_ROOT = resource_path(REPO_ROOT, "opsx", "opsx_runtime")
EXECUTOR_ROOT = resource_path(REPO_ROOT, "tools", "cursor_executor")
for extra in (RUNTIME_ROOT, EXECUTOR_ROOT):
    extra_s = str(extra)
    if extra_s not in sys.path:
        sys.path.insert(0, extra_s)

from agents.adapters.provider_bootstrap import ensure_adapter_factories_registered  # noqa: E402
from agents.adapters.reconcile_async_client import launch_bridge_client  # noqa: E402
from cursor_executor.auth import resolve_auth  # noqa: E402
from cursor_executor.bridge_local import bridge_launch_kwargs  # noqa: E402

ensure_adapter_factories_registered()

TERMINAL = frozenset({"finished", "error", "cancelled", "expired"})
DEFAULT_LOG = resource_path(REPO_ROOT, "opsx", "runs", "verify", "agent-run-monitor.log")


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _ndjson_stats(path: Path) -> tuple[int, int, str]:
    if not path.is_file():
        return 0, 0, "-"
    stat = path.stat()
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
    mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
    return stat.st_size, lines, mtime


def _append_log(log_path: Path, line: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
        handle.flush()


async def _fetch_run_status(
    *, run_id: str, workspace: str, api_key: str
) -> tuple[str, int | None, str | None]:
    async with await launch_bridge_client(
        **bridge_launch_kwargs(agent_cwd=workspace)
    ) as client:
        options = {"runtime": "local", "cwd": workspace, "apiKey": api_key}
        run = await client.get_run(run_id, options)
        status = str(getattr(run, "status", "") or "")
        raw_duration = getattr(run, "duration_ms", None)
        duration_ms = int(raw_duration) if raw_duration else None
        agent_id = str(getattr(run, "agent_id", "") or "") or None
        return status, duration_ms, agent_id


async def monitor_loop(args: argparse.Namespace) -> int:
    auth = resolve_auth()
    if not auth.api_key:
        _append_log(args.log, f"{_ts()} ERROR CURSOR_API_KEY missing")
        return 2

    workspace = str(REPO_ROOT.resolve())
    live_path = Path(args.live_ndjson).resolve()
    agent_id = args.agent_id

    _append_log(
        args.log,
        f"{_ts()} START run_id={args.run_id} agent_id={agent_id or '-'} live={live_path}",
    )

    while True:
        tick = _ts()
        nbytes, nlines, nmtime = _ndjson_stats(live_path)
        try:
            status, duration_ms, observed_agent = await _fetch_run_status(
                run_id=args.run_id, workspace=workspace, api_key=auth.api_key
            )
            if observed_agent and not agent_id:
                agent_id = observed_agent
            dur = f" duration_ms={duration_ms}" if duration_ms is not None else ""
            _append_log(
                args.log,
                f"{tick} status={status}{dur} ndjson_bytes={nbytes} "
                f"ndjson_lines={nlines} ndjson_mtime={nmtime}",
            )
            if status in TERMINAL:
                note = f" agent_id={agent_id}" if agent_id else ""
                _append_log(
                    args.log,
                    f"{_ts()} TERMINAL={status} fetch_conversation if needed{note}",
                )
                return 0
        except Exception as exc:
            _append_log(
                args.log,
                f"{tick} get_run_error={type(exc).__name__}: {exc} "
                f"ndjson_bytes={nbytes} ndjson_lines={nlines} ndjson_mtime={nmtime}",
            )

        await asyncio.sleep(args.interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor adapter run status")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--agent-id", default=None)
    parser.add_argument("--live-ndjson", required=True)
    parser.add_argument("--interval", type=float, default=45.0)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parsed = parser.parse_args()
    parsed.log = Path(parsed.log).resolve()
    raise SystemExit(asyncio.run(monitor_loop(parsed)))


if __name__ == "__main__":
    main()
