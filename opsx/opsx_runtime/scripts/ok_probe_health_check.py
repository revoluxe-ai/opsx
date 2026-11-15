"""Диагностика transport/stream/wait для ``ok_probe`` (composer-2.5).

Один live-hop через ``OkProbeAgent`` и тот же bridge, что monitor/smoke.
Явный бюджет ``wait_cursor_run_with_live_log`` (без wall-clock kill до timeout
адаптера). По завершении — ``export_run_conversation`` и одна JSON-строка сводки
для RCA (recv vs finish vs validated OK).
"""

from __future__ import annotations

from opsx.project_context import resource_path, project_root

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

# Включить recv-отладку до импорта stream-адаптера.
os.environ.setdefault("OPSX_STREAM_DEBUG", "1")

REPO_ROOT = project_root()
RUNTIME_ROOT = resource_path(REPO_ROOT, "opsx", "opsx_runtime")
RUNS_VERIFY = resource_path(REPO_ROOT, "opsx", "runs", "verify")
SCRIPTS_ROOT = RUNTIME_ROOT / "scripts"
EXECUTOR_ROOT = resource_path(REPO_ROOT, "tools", "cursor_executor")

for extra in (RUNTIME_ROOT, SCRIPTS_ROOT, EXECUTOR_ROOT):
    extra_s = str(extra)
    if extra_s not in sys.path:
        sys.path.insert(0, extra_s)

from agents.adapters.provider_bootstrap import ensure_adapter_factories_registered  # noqa: E402
from agents.adapters.provider_registry import call_reconcile_hook, reconcile_hook  # noqa: E402
from agents.adapters.provider_registry import build_review_adapter  # noqa: E402
from agents.adapters.reconcile_async_client import launch_bridge_client  # noqa: E402
from agents.abc_agent import ABCAgent  # noqa: E402
from agents.adapters.abc_adapter import schema_format_reminder  # noqa: E402
from runtime_bootstrap import ensure_provider_wheel_importable  # noqa: E402

ensure_provider_wheel_importable()
ensure_adapter_factories_registered()
from agents.state import GraphState  # noqa: E402
from agents.utils.response import parse_agent_json  # noqa: E402
from agents.verify.ok_probe.agent import OkProbeAgent  # noqa: E402
from agents.verify.ok_probe.prompt.get_prompt import get_prompt  # noqa: E402
from agents.verify.ok_probe.schemas import OkProbeAnswer  # noqa: E402
from cursor_executor.auth import resolve_auth  # noqa: E402
from cursor_executor.bridge_local import (  # noqa: E402
    agent_local_options,
    bridge_launch_kwargs,
)
from export_run_conversation import export_run_conversation  # noqa: E402

_CONFIGS_ROOT = resource_path(REPO_ROOT, "opsx", "configs")
if str(_CONFIGS_ROOT) not in sys.path:
    sys.path.insert(0, str(_CONFIGS_ROOT))

from model_config.model_presets import (  # noqa: E402
    GROK_46_LOW_PRESET_SLUG,
    GROK_47_LOW_PRESET_SLUG,
    grok_46_low,
    grok_low,
    super_low_model,
)
from model_config.models.claude_haiku import (  # noqa: E402
    ClaudeHaiku,
    Context as HaikuContext,
    Reasoning as HaikuReasoning,
)
from model_config.models.base_model_setting import BaseModelSetting  # noqa: E402
from model_config.resolve_model import ModelResolveResult  # noqa: E402
from agents.adapters.hop_model_selection import model_selection_for_hop  # noqa: E402

DEFAULT_MODEL_SLUG = "composer-2.5"
_haiku_low_probe = ClaudeHaiku(
    context=HaikuContext.CTX_300K,
    reasoning=HaikuReasoning.LOW,
)
_HEALTH_CHECK_MODELS: dict[str, BaseModelSetting] = {
    DEFAULT_MODEL_SLUG: super_low_model,
    super_low_model.name: super_low_model,
    grok_low.name: grok_low,
    GROK_47_LOW_PRESET_SLUG: grok_low,
    grok_46_low.name: grok_46_low,
    GROK_46_LOW_PRESET_SLUG: grok_46_low,
    _haiku_low_probe.name: _haiku_low_probe,
}
_TERMINAL_RUN_STATUSES = frozenset({"finished", "error", "cancelled", "expired"})


def _model_selection(model_id: str) -> dict[str, Any]:
    slug = model_id.strip() or DEFAULT_MODEL_SLUG
    preset = _HEALTH_CHECK_MODELS.get(slug)
    if preset is None:
        raise ValueError(f"unsupported model for health check: {slug!r}")
    return model_selection_for_hop(ModelResolveResult(model=preset))


def _parse_ndjson_metrics(path: Path) -> dict[str, Any]:
    """Подсчёт событий и run_id из live NDJSON."""
    recv_event_count = 0
    thinking_chunks = 0
    assistant_chunks = 0
    run_id: str | None = None
    last_ts: datetime | None = None
    if not path.is_file():
        return {
            "recv_event_count": 0,
            "thinking_chunks": 0,
            "assistant_chunks": 0,
            "run_id": None,
            "last_event_age_sec": None,
        }
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts_raw = row.get("ts")
            if isinstance(ts_raw, str):
                try:
                    last_ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                except ValueError:
                    pass
            kind = row.get("kind")
            if kind in {"thinking", "interaction", "tool_call", "assistant", "step"}:
                recv_event_count += 1
            if kind == "thinking":
                thinking_chunks += 1
            if kind == "interaction" and row.get("update_type") == "thinking-delta":
                thinking_chunks += 1
            if kind == "assistant":
                assistant_chunks += 1
            if kind == "run_meta" and row.get("run_id"):
                run_id = str(row["run_id"])
    last_age: float | None = None
    if last_ts is not None:
        last_age = (
            datetime.now(timezone.utc) - last_ts.astimezone(timezone.utc)
        ).total_seconds()
    return {
        "recv_event_count": recv_event_count,
        "thinking_chunks": thinking_chunks,
        "assistant_chunks": assistant_chunks,
        "run_id": run_id,
        "last_event_age_sec": last_age,
    }


def _conversation_nonempty(path: Path | None) -> bool:
    if path is None or not path.is_file():
        return False
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return False
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        return len(text) > 20
    if isinstance(loaded, list):
        return len(loaded) > 0
    if isinstance(loaded, dict):
        if loaded.keys() <= {"run_id", "error"}:
            return False
        return True
    return bool(text)


async def _poll_run_status_while_active(
    *,
    run_id_holder: dict[str, str | None],
    stream_log: Path,
    workspace: str,
    api_key: str,
    stop_event: asyncio.Event,
    interval_sec: float = 15.0,
) -> dict[str, Any]:
    """Параллельный опрос GetRun (дополнение к ``run.wait()`` в stream-адаптере)."""
    samples: list[dict[str, str | None]] = []
    while not stop_event.is_set():
        if not run_id_holder.get("run_id") and stream_log.is_file():
            metrics = _parse_ndjson_metrics(stream_log)
            rid = metrics.get("run_id")
            if rid:
                run_id_holder["run_id"] = str(rid)
        run_id = run_id_holder.get("run_id")
        if run_id:
            status = await _get_run_status(run_id, workspace, api_key)
            samples.append({"run_id": run_id, "status": status})
            if status and status.lower() in _TERMINAL_RUN_STATUSES:
                break
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_sec)
        except asyncio.TimeoutError:
            continue
    return {"poll_samples": samples, "poll_count": len(samples)}


async def _get_run_status(run_id: str, workspace: str, api_key: str) -> str | None:
    """Статус run на сервере (GetRun)."""
    try:
        async with await launch_bridge_client(
            **bridge_launch_kwargs(agent_cwd=workspace)
        ) as client:
            options = {"runtime": "local", "cwd": workspace, "apiKey": api_key}
            fetched = await client.get_run(run_id, options)
            return str(getattr(fetched, "status", "") or "")
    except Exception:
        return None


def _hypothesis(
    *,
    recv_event_count: int,
    wait_status: str,
    validated_ok_answer: bool,
    run_status: str | None,
) -> str:
    """Краткая метка для RCA."""
    if recv_event_count == 0:
        return "no_recv"
    terminal = (run_status or "").lower() in _TERMINAL_RUN_STATUSES
    if validated_ok_answer and wait_status == "ok":
        return "finished_ok_json"
    if wait_status == "timeout":
        return "recv_but_wait_timeout"
    if recv_event_count > 0 and not terminal and wait_status != "ok":
        return "recv_but_no_finish"
    if terminal and not validated_ok_answer:
        return "finished_without_ok_json"
    return "recv_inconclusive"


def _validated_ok_from_minimal_probe(probe: Mapping[str, Any]) -> bool:
    """Проверить ``OkProbeAnswer`` по текстам minimal-адаптера."""
    candidates: list[str] = []
    for key in ("assistant_text", "thinking_text", "wait_result_text"):
        raw = probe.get(key)
        if isinstance(raw, str) and raw.strip():
            candidates.append(raw)
    snippets = probe.get("json_snippets")
    if isinstance(snippets, list):
        candidates.extend(str(item) for item in snippets if item)
    for text in candidates:
        try:
            parse_agent_json(text, OkProbeAnswer)
            return True
        except Exception:
            continue
    return False


async def _run_health_minimal(
    *,
    repo_root: Path,
    run_dir: Path,
    timeout_seconds: float,
    model_id: str,
) -> dict[str, Any]:
    """Тот же ok_probe prompt, но transport через ``run_minimal_probe``."""
    model_slug = model_id.strip() or DEFAULT_MODEL_SLUG
    session_id = uuid.uuid4().hex[:12]
    thread_id = f"ok-probe-minimal-{session_id}"
    stream_log = run_dir / f"ok-probe-minimal-{session_id}.live.ndjson"
    run_dir.mkdir(parents=True, exist_ok=True)

    auth = resolve_auth()
    if not auth.api_key:
        raise RuntimeError("CURSOR_API_KEY is missing")

    workspace = str(repo_root.resolve())
    model = _model_selection(model_id)
    prompt = get_prompt().rstrip() + "\n\n" + schema_format_reminder(OkProbeAnswer)
    stream_label = f"minimal-{session_id}|ok_probe model={model_slug}"
    stream_wait_mode = (
        "minimal: create_task(run.wait()) + run.observe().__anext__(); "
        "no idle GetRun recovery"
    )

    wait_status = "ok"
    agent_error: str | None = None
    validated_ok_answer = False
    usage: dict[str, Any] | None = None
    execution_status: str | None = None
    run_id: str | None = None
    probe_out: dict[str, Any] | None = None
    run_id_holder: dict[str, str | None] = {"run_id": None}
    poll_stop = asyncio.Event()
    poll_task: asyncio.Task[dict[str, Any]] | None = None

    print(
        f"HEALTH ok_probe minimal start timeout={timeout_seconds}s model={model_slug}",
        flush=True,
    )
    poll_task = asyncio.create_task(
        _poll_run_status_while_active(
            run_id_holder=run_id_holder,
            stream_log=stream_log,
            workspace=workspace,
            api_key=auth.api_key,
            stop_event=poll_stop,
        )
    )
    try:
        local = agent_local_options(agent_cwd=workspace)
        async with await launch_bridge_client(
            **bridge_launch_kwargs(agent_cwd=workspace)
        ) as client:
            probe_out = await call_reconcile_hook(
                "run_minimal_probe",
                client=client,
                prompt=prompt,
                model=model,
                local=local,
                api_key=auth.api_key,
                tools=(),
                stream_log=stream_log,
                stream_label=stream_label,
                timeout_seconds=timeout_seconds,
            )
        run_id = str(probe_out.get("run_id") or "") or None
        if run_id:
            run_id_holder["run_id"] = run_id
        terminal = probe_out.get("wait_result")
        execution_status = str(getattr(terminal, "status", "") or "")
        usage = (
            probe_out.get("usage")
            if isinstance(probe_out.get("usage"), dict)
            else None
        )
        validated_ok_answer = _validated_ok_from_minimal_probe(probe_out)
        wait_status = str(probe_out.get("wait_status") or "ok")
    except asyncio.TimeoutError as exc:
        wait_status = "timeout"
        agent_error = str(exc) or type(exc).__name__
    except Exception as exc:
        wait_status = "agent_error"
        agent_error = f"{type(exc).__name__}: {exc}"
    finally:
        poll_stop.set()
        poll_outcome: dict[str, Any] | None = None
        if poll_task is not None:
            try:
                poll_outcome = await poll_task
            except Exception as poll_exc:
                poll_outcome = {"poll_error": f"{type(poll_exc).__name__}: {poll_exc}"}

    metrics = _parse_ndjson_metrics(stream_log)
    if not run_id and metrics.get("run_id"):
        run_id = str(metrics["run_id"])
        run_id_holder["run_id"] = run_id

    conv_path = ABCAgent.conversation_path_for_stream_log(stream_log)
    conversation_nonempty = _conversation_nonempty(conv_path)

    run_status: str | None = None
    export_path: Path | None = None
    if run_id:
        run_status = await _get_run_status(run_id, workspace, auth.api_key)
        print(
            f"HEALTH after_wait run_id={run_id} get_run_status={run_status!r} "
            f"conversation_nonempty={conversation_nonempty} "
            f"last_event_age_sec={metrics.get('last_event_age_sec')}",
            flush=True,
        )
        export_path = run_dir / f"{run_id}.conversation.export.json"
        await export_run_conversation(
            run_id=run_id,
            out_path=export_path,
            workspace=workspace,
            api_key=auth.api_key,
        )
        if not conversation_nonempty:
            conversation_nonempty = _conversation_nonempty(export_path)

    hypothesis = _hypothesis(
        recv_event_count=int(metrics["recv_event_count"]),
        wait_status=wait_status,
        validated_ok_answer=validated_ok_answer,
        run_status=run_status,
    )

    thinking_len = len(str((probe_out or {}).get("thinking_text") or ""))
    assistant_len = len(str((probe_out or {}).get("assistant_text") or ""))

    summary = {
        "hypothesis": hypothesis,
        "wait_status": wait_status,
        "adapter": "minimal",
        "stream_wait_mode": stream_wait_mode,
        "get_run_poll": poll_outcome,
        "run_id": run_id,
        "run_status_after_get_run": run_status,
        "execution_status": execution_status,
        "recv_event_count": metrics["recv_event_count"],
        "thinking_chunks": metrics["thinking_chunks"],
        "assistant_chunks": metrics["assistant_chunks"],
        "thinking_text_len": thinking_len,
        "assistant_text_len": assistant_len,
        "event_counts": (probe_out or {}).get("event_counts"),
        "last_stream_event_age_sec": metrics.get("last_event_age_sec"),
        "conversation_json_nonempty": conversation_nonempty,
        "validated_ok_answer": validated_ok_answer,
        "usage": usage,
        "agent_error": agent_error,
        "stream_log": str(stream_log),
        "conversation_export": str(export_path) if export_path else None,
        "model_slug": model_slug,
        "thread_id": thread_id,
    }
    return summary


async def _run_health(
    *,
    repo_root: Path,
    run_dir: Path,
    timeout_seconds: float,
    model_id: str,
) -> dict[str, Any]:
    model_slug = model_id.strip() or DEFAULT_MODEL_SLUG
    session_id = uuid.uuid4().hex[:12]
    thread_id = f"ok-probe-health-{session_id}"
    stream_log = run_dir / f"ok-probe-health-{session_id}.live.ndjson"
    run_dir.mkdir(parents=True, exist_ok=True)

    auth = resolve_auth()
    if not auth.api_key:
        raise RuntimeError("CURSOR_API_KEY is missing")

    workspace = str(repo_root.resolve())
    adapter = build_review_adapter(timeout_seconds=timeout_seconds, allow_git_shell=False)
    agent = OkProbeAgent(adapter)
    state: GraphState = {
        "repository_path": workspace,
        "workspace": workspace,
        "model": _model_selection(model_id),
        "stream_log": str(stream_log),
        "stream_label": f"health-{session_id}|ok_probe model={model_slug}",
        "thread_id": thread_id,
    }

    wait_status = "ok"
    agent_error: str | None = None
    result: dict[str, Any] | None = None
    validated_ok_answer = False
    usage: dict[str, Any] | None = None
    execution_status: str | None = None
    run_id: str | None = None
    run_id_holder: dict[str, str | None] = {"run_id": None}
    poll_stop = asyncio.Event()
    poll_task: asyncio.Task[dict[str, Any]] | None = None
    stream_wait_mode = (
        "parallel_wait_live_run: WaitLiveRun + __aiter__ (run.wait() не параллелится "
        "с __aiter__ — один generator); hop deadline asyncio.wait_for; idle 90s "
        "по полезным событиям; overall timeout → CancelRun(run_id) + run.wait() + GetRun"
    )

    print(
        f"HEALTH ok_probe start timeout={timeout_seconds}s model={model_slug}",
        flush=True,
    )
    poll_task = asyncio.create_task(
        _poll_run_status_while_active(
            run_id_holder=run_id_holder,
            stream_log=stream_log,
            workspace=workspace,
            api_key=auth.api_key,
            stop_event=poll_stop,
        )
    )
    try:
        result = await agent.run(state)
        run_id = str(result.get("run_id") or "") or None
        if run_id:
            run_id_holder["run_id"] = run_id
        execution_status = str(result.get("execution_status") or "")
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else None
        answer_raw = result.get("answer")
        if isinstance(answer_raw, dict):
            try:
                OkProbeAnswer.model_validate(answer_raw)
                validated_ok_answer = True
            except Exception:
                validated_ok_answer = False
    except asyncio.TimeoutError as exc:
        wait_status = "timeout"
        agent_error = str(exc) or type(exc).__name__
    except Exception as exc:
        wait_status = "agent_error"
        agent_error = f"{type(exc).__name__}: {exc}"
    finally:
        poll_stop.set()
        poll_outcome: dict[str, Any] | None = None
        if poll_task is not None:
            try:
                poll_outcome = await poll_task
            except Exception as poll_exc:
                poll_outcome = {"poll_error": f"{type(poll_exc).__name__}: {poll_exc}"}

    metrics = _parse_ndjson_metrics(stream_log)
    if not run_id and metrics.get("run_id"):
        run_id = str(metrics["run_id"])
        run_id_holder["run_id"] = run_id

    conv_path = ABCAgent.conversation_path_for_stream_log(stream_log)
    conversation_nonempty = _conversation_nonempty(conv_path)

    run_status: str | None = None
    export_path: Path | None = None
    if run_id:
        run_status = await _get_run_status(run_id, workspace, auth.api_key)
        print(
            f"HEALTH after_wait run_id={run_id} get_run_status={run_status!r} "
            f"conversation_nonempty={conversation_nonempty} "
            f"last_event_age_sec={metrics.get('last_event_age_sec')}",
            flush=True,
        )
        export_path = run_dir / f"{run_id}.conversation.export.json"
        await export_run_conversation(
            run_id=run_id,
            out_path=export_path,
            workspace=workspace,
            api_key=auth.api_key,
        )
        if not conversation_nonempty:
            conversation_nonempty = _conversation_nonempty(export_path)

    hypothesis = _hypothesis(
        recv_event_count=int(metrics["recv_event_count"]),
        wait_status=wait_status,
        validated_ok_answer=validated_ok_answer,
        run_status=run_status,
    )

    summary = {
        "hypothesis": hypothesis,
        "wait_status": wait_status,
        "adapter": "default",
        "stream_wait_mode": stream_wait_mode,
        "get_run_poll": poll_outcome,
        "stream_unknown_tally": (reconcile_hook("stream_debug_unknown_tally") or (lambda: {}))(),
        "run_id": run_id,
        "run_status_after_get_run": run_status,
        "execution_status": execution_status,
        "recv_event_count": metrics["recv_event_count"],
        "thinking_chunks": metrics["thinking_chunks"],
        "assistant_chunks": metrics["assistant_chunks"],
        "last_stream_event_age_sec": metrics.get("last_event_age_sec"),
        "conversation_json_nonempty": conversation_nonempty,
        "validated_ok_answer": validated_ok_answer,
        "usage": usage,
        "agent_error": agent_error,
        "stream_log": str(stream_log),
        "conversation_export": str(export_path) if export_path else None,
        "model_slug": model_slug,
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Minimal ok_probe transport/stream health check (live adapter)"
    )
    parser.add_argument("--repo", type=Path, default=REPO_ROOT)
    parser.add_argument("--run-dir", type=Path, default=None)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--model-id", default=DEFAULT_MODEL_SLUG)
    parser.add_argument(
        "--adapter",
        choices=("default", "minimal"),
        default="default",
        help="default=OkProbeAgent+wait_cursor_run_with_live_log; minimal=run_minimal_probe",
    )
    args = parser.parse_args()
    repo = args.repo.resolve()
    run_dir = (
        args.run_dir.resolve()
        if args.run_dir is not None
        else RUNS_VERIFY / f"run-ok-probe-health-{uuid.uuid4().hex[:12]}"
    )

    runner = _run_health_minimal if args.adapter == "minimal" else _run_health
    summary = asyncio.run(
        runner(
            repo_root=repo,
            run_dir=run_dir,
            timeout_seconds=args.timeout,
            model_id=args.model_id,
        )
    )
    print(f"HEALTH_SUMMARY {json.dumps(summary, ensure_ascii=False)}", flush=True)


if __name__ == "__main__":
    main()
