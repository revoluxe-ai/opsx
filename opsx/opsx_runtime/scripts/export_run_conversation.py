"""Экспорт полной беседы adapter run через GetRunConversation (read-only).

Скрипт поднимает локальный bridge так же, как ``monitor_cursor_run.py``,
вызывает ``get_run`` + ``conversation_json()`` и пишет JSON на диск.
При таймауте или «пустом» ответе дополнительно выгружает строки из
``jsonl-store`` (run_events / checkpoints), отфильтрованные по ``run_id``.
"""

from __future__ import annotations

from opsx.project_context import resource_path, project_root

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = project_root()
OPSX_RUNTIME = resource_path(REPO_ROOT, "opsx", "opsx_runtime")
EXECUTOR_ROOT = resource_path(REPO_ROOT, "tools", "cursor_executor")

for extra in (OPSX_RUNTIME, EXECUTOR_ROOT):
    extra_s = str(extra)
    if extra_s not in sys.path:
        sys.path.insert(0, extra_s)

from agents.adapters.provider_bootstrap import ensure_adapter_factories_registered  # noqa: E402
from agents.adapters.reconcile_async_client import launch_bridge_client  # noqa: E402
from cursor_executor.auth import resolve_auth  # noqa: E402
from cursor_executor.bridge_local import (  # noqa: E402
    bridge_launch_kwargs,
    jsonl_store_root,
)

DEFAULT_VERIFY_DIR = resource_path(REPO_ROOT, "opsx", "runs", "verify")
CONVERSATION_TIMEOUT_SEC = 120.0
NDJSON_SOURCES = ("run_events.ndjson", "checkpoints.ndjson")

ensure_adapter_factories_registered()


def _default_out_path(run_id: str) -> Path:
    return DEFAULT_VERIFY_DIR / f"{run_id}.conversation.json"


def _parse_loaded(raw: str) -> Any | None:
    text = raw.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _is_stub_conversation(raw: str) -> bool:
    """Эвристика: ответ API без полноценной истории turns."""
    if not raw.strip():
        return True
    loaded = _parse_loaded(raw)
    if loaded is None:
        return True
    if isinstance(loaded, dict):
        keys = set(loaded.keys())
        if keys <= {"run_id", "error"} or keys == {"error"}:
            return True
        if "error" in loaded and len(keys) <= 3:
            return True
    if isinstance(loaded, list) and len(loaded) == 0:
        return True
    return False


def _summarize_written(raw: str) -> str:
    loaded = _parse_loaded(raw)
    if isinstance(loaded, list):
        return f"turns={len(loaded)}"
    if isinstance(loaded, dict):
        return f"keys={sorted(loaded.keys())}"
    return "keys=unparsed"


def _conversation_has_tool_calls(raw: str) -> bool:
    loaded = _parse_loaded(raw)
    if isinstance(loaded, list):
        blob = json.dumps(loaded, ensure_ascii=False)
    elif isinstance(loaded, dict):
        blob = json.dumps(loaded, ensure_ascii=False)
    else:
        blob = raw
    lowered = blob.lower()
    normalized = lowered.replace("_", "").replace(" ", "")
    return (
        "tool_call" in lowered
        or "toolcall" in normalized
        or '"type":"tool"' in lowered
        or '"type": "tool"' in lowered
    )


def _line_matches_run_id(line: str, run_id: str) -> bool:
    if run_id not in line:
        return False
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return run_id in line
    if isinstance(obj, dict):
        for key in ("runId", "run_id"):
            if str(obj.get(key, "")) == run_id:
                return True
        payload = obj.get("payload")
        if isinstance(payload, dict):
            for key in ("runId", "run_id"):
                if str(payload.get(key, "")) == run_id:
                    return True
            message = payload.get("message")
            if isinstance(message, dict) and str(message.get("run_id", "")) == run_id:
                return True
    return run_id in line


def _scan_tool_calls_in_ndjson(path: Path, run_id: str) -> bool:
    if not path.is_file():
        return False
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if not _line_matches_run_id(line, run_id):
                continue
            lowered = line.lower()
            if "tool_call" in lowered or '"type":"tool_call"' in lowered:
                return True
            if '"name":"tool' in lowered and "shell" in lowered:
                return True
    return False


def dump_run_ndjson_fallback(*, run_id: str, out_conversation: Path) -> tuple[Path | None, str]:
    """Скопировать строки jsonl-store для ``run_id`` в ``*.run-events.jsonl``.

    Returns:
        Путь к файлу и текст о том, где есть tool calls (или ``none``).
    """
    store = jsonl_store_root()
    target = out_conversation.with_suffix(".run-events.jsonl")
    matched = 0
    tool_sources: list[str] = []
    with target.open("w", encoding="utf-8", newline="\n") as out:
        for name in NDJSON_SOURCES:
            src = store / name
            if not src.is_file():
                continue
            with src.open(encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    stripped = line.rstrip("\n\r")
                    if not stripped or not _line_matches_run_id(stripped, run_id):
                        continue
                    out.write(stripped + "\n")
                    matched += 1
            if _scan_tool_calls_in_ndjson(src, run_id):
                tool_sources.append(name)
    if matched == 0:
        if target.is_file():
            target.unlink(missing_ok=True)
        return None, "none (no ndjson rows for run_id)"
    tools_note = ", ".join(tool_sources) if tool_sources else "none in filtered rows"
    return target, tools_note


async def export_run_conversation(
    *,
    run_id: str,
    out_path: Path,
    workspace: str,
    api_key: str,
    conversation_timeout_sec: float = CONVERSATION_TIMEOUT_SEC,
) -> tuple[str, bool, bool]:
    """Экспортировать беседу run через bridge + ``AsyncRun.conversation_json()``.

    Args:
        run_id: Идентификатор run (``run-…``), не ``agent-…``.
        out_path: Куда записать JSON (создаёт родительские каталоги).
        workspace: Корень репозитория для ``bridge_launch_kwargs``.
        api_key: Cursor API key.
        conversation_timeout_sec: Таймаут RPC GetRunConversation.

    Returns:
        Кортеж ``(raw_json, fetch_failed, is_stub)`` — тело ответа и флаги качества.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw = ""
    fetch_failed = False
    is_stub = False

    async with await launch_bridge_client(
        **bridge_launch_kwargs(agent_cwd=workspace)
    ) as client:
        options = {"runtime": "local", "cwd": workspace, "apiKey": api_key}
        run = await client.get_run(run_id, options)
        try:
            raw = await asyncio.wait_for(
                run.conversation_json(), timeout=conversation_timeout_sec
            )
        except Exception as exc:
            fetch_failed = True
            raw = json.dumps(
                {
                    "run_id": run_id,
                    "error": f"{type(exc).__name__}: {exc}",
                },
                ensure_ascii=False,
                indent=2,
            )

    is_stub = fetch_failed or _is_stub_conversation(raw)
    payload = raw if raw.endswith("\n") else raw + "\n"
    out_path.write_text(payload, encoding="utf-8")
    return raw, fetch_failed, is_stub


def _print_summary(out_path: Path, raw: str) -> None:
    size = out_path.stat().st_size
    print(f"{out_path} bytes={size} {_summarize_written(raw)}")


async def _async_main(args: argparse.Namespace) -> int:
    auth = resolve_auth()
    if not auth.api_key:
        print("ERROR CURSOR_API_KEY missing", file=sys.stderr)
        return 2

    workspace = str(REPO_ROOT.resolve())
    out_path = Path(args.out).resolve() if args.out else _default_out_path(args.run_id)

    raw, fetch_failed, is_stub = await export_run_conversation(
        run_id=args.run_id,
        out_path=out_path,
        workspace=workspace,
        api_key=auth.api_key,
        conversation_timeout_sec=args.timeout,
    )
    _print_summary(out_path, raw)

    if fetch_failed or is_stub:
        fallback_path, tools_note = dump_run_ndjson_fallback(
            run_id=args.run_id, out_conversation=out_path
        )
        if fallback_path is not None:
            fb_size = fallback_path.stat().st_size
            print(
                f"fallback={fallback_path} bytes={fb_size} "
                f"tool_calls_in={tools_note}"
            )
        else:
            print(f"fallback=skipped tool_calls_in={tools_note}")

    has_tools = _conversation_has_tool_calls(raw)
    print(f"tool_calls_in_conversation={'yes' if has_tools else 'no'}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Export Cursor run conversation (GetRunConversation, read-only)"
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--timeout",
        type=float,
        default=CONVERSATION_TIMEOUT_SEC,
        help="GetRunConversation timeout seconds",
    )
    parsed = parser.parse_args()
    raise SystemExit(asyncio.run(_async_main(parsed)))


if __name__ == "__main__":
    main()
