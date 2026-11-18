"""Единая политика LangGraph checkpoint/resume для OPSX runtime.

Стабильный ``thread_id`` и выбор самого продвинутого durable checkpoint
гарантируют, что перезапуск workflow не повторяет уже закоммиченные узлы.
"""

from __future__ import annotations

from opsx.project_context import resource_path

import inspect
import json
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from langgraph._internal._constants import CONFIG_KEY_CHECKPOINTER
from langgraph._internal._runnable import RunnableCallable
from langgraph.constants import END
from langgraph.graph._branch import BranchSpec
from langgraph.pregel._write import ChannelWrite, ChannelWriteEntry
from langgraph.types import Command, Send, StateSnapshot

from agents.state import GraphState
from workflows.repo_paths import graph_repo_path_patch, resolve_opsx_repository_root

logger = logging.getLogger(__name__)

# verify-impl / parent PP: durable LangGraph checkpoint отключён — каждый CLI-запуск
# без SQLite read/write, но с in-memory saver для stale-end continuation в том же hop.
LANGGRAPH_DURABLE_CHECKPOINT_ENABLED = False


def durable_langgraph_checkpoint_enabled() -> bool:
    """Возвращает, разрешён ли durable LangGraph checkpoint для verify-impl entrypoints."""
    return LANGGRAPH_DURABLE_CHECKPOINT_ENABLED


def graph_invoke_continuation_enabled(graph: Any) -> bool:
    """True, если после ``ainvoke`` разрешён stale-end continue loop (только durable SQLite)."""
    _ = graph
    return durable_langgraph_checkpoint_enabled()


_BRANCH_TO_PREFIX = "branch:to:"

_LEGACY_APPLY_NODE = "opsx_apply"

_LEGACY_RUN_SUFFIX = re.compile(r"^[0-9a-f]{12}$")


def format_checkpoint_snapshot_summary(
    snapshot: Any,
    config: Mapping[str, Any] | None = None,
) -> str:
    """Сжатое описание LangGraph ``StateSnapshot`` для checkpoint-логов."""
    if snapshot is None:
        return "snapshot=none"
    configurable = (config or {}).get("configurable") or {}
    snap_config = getattr(snapshot, "config", None)
    if not configurable and isinstance(snap_config, Mapping):
        configurable = snap_config.get("configurable") or {}
    ckpt_id = str(configurable.get("checkpoint_id") or "-")
    ckpt_ns = str(configurable.get("checkpoint_ns") or "-")
    next_nodes = tuple(getattr(snapshot, "next", None) or ())
    tasks = getattr(snapshot, "tasks", None) or ()
    values = getattr(snapshot, "values", None) or {}
    verdict = str(values.get("verdict") or values.get("quality_gate_verdict") or "-")
    scenario_verdict = str(values.get("scenario_coverage_verdict") or "-")
    stop = str(values.get("verify_stop_reason") or "-")
    return (
        f"checkpoint_id={ckpt_id} checkpoint_ns={ckpt_ns} next={next_nodes} "
        f"tasks_count={len(tasks)} impl_or_quality_verdict={verdict} "
        f"scenario_coverage_verdict={scenario_verdict} verify_stop_reason={stop}"
    )


def _format_tasks_summary(tasks: Sequence[Any]) -> str:
    """Краткий список ``StateSnapshot.tasks`` для диагностики resume."""
    if not tasks:
        return "tasks=[]"
    parts: list[str] = []
    for index, task in enumerate(tasks[:8]):
        name = getattr(task, "name", "?")
        path = getattr(task, "path", ())
        parts.append(f"{index}:{name} path={path}")
    suffix = f" ...+{len(tasks) - 8}" if len(tasks) > 8 else ""
    return f"tasks=[{', '.join(parts)}{suffix}]"


def _thread_id_from_config(config: Mapping[str, Any]) -> str:
    configurable = config.get("configurable") or {}
    return str(configurable.get("thread_id") or "-")


def normalize_stale_apply_successor(graph: Any, successor: str) -> str:
    """Сопоставить устаревший id узла apply-hop с ``apply`` в текущем графе."""
    if successor == _LEGACY_APPLY_NODE and "apply" in graph.nodes:
        return "apply"
    return successor


async def _scrub_stale_root_pending_goto(
    graph: Any,
    config: Mapping[str, Any],
    snapshot: Any,
) -> None:
    """Сбросить устаревший pending ``Command(goto=...)`` на корне thread перед subgraph resume."""
    update = getattr(graph, "aupdate_state", None)
    if update is None or not snapshot.values:
        return
    configurable = config.get("configurable") or {}
    if configurable.get("checkpoint_ns"):
        return
    try:
        _ = await update(
            dict(config),
            dict(snapshot.values),
            as_node=END,
        )
    except Exception:
        return


@dataclass(frozen=True)
class WorkflowResumeScope:
    """Ключ run identity: один scope — один durable LangGraph thread."""

    kind: str
    change_id: str | None = None
    mission_id: str | None = None


@dataclass(frozen=True)
class ResolvedWorkflowRun:
    """Выбранные run_dir, sqlite checkpoint и thread_id для ``ainvoke``."""

    run_dir: Path
    checkpoint_path: Path
    thread_id: str
    resumed: bool


@dataclass(frozen=True)
class StaleEndResumePlan:
    """Продолжение run после устаревшего END: граф, config и ``Command(goto=...)``."""

    target_graph: Any
    config: dict[str, Any]
    successor: str


def _graph_checkpointer(graph: Any, config: Mapping[str, Any]) -> Any | None:
    checkpointer = getattr(graph, "checkpointer", None)
    if checkpointer is not None:
        return checkpointer
    configurable = config.get("configurable")
    if isinstance(configurable, Mapping):
        return configurable.get(CONFIG_KEY_CHECKPOINTER)
    return None


def _with_checkpointer_config(
    graph: Any,
    config: Mapping[str, Any],
    checkpointer: Any | None = None,
) -> dict[str, Any]:
    """Добавить checkpointer в ``config`` для ``aget_state`` nested subgraph."""
    merged = dict(config)
    configurable = dict(merged.get("configurable") or {})
    saver = checkpointer or _graph_checkpointer(graph, merged)
    if saver is not None:
        configurable[CONFIG_KEY_CHECKPOINTER] = saver
    merged["configurable"] = configurable
    return merged


def stable_thread_id(scope: WorkflowResumeScope) -> str:
    """Вернуть канонический ``thread_id`` для нового durable run в scope."""
    if scope.kind == "verify_impl":
        if not scope.change_id:
            raise ValueError("verify_impl scope requires change_id")
        return f"verify-impl:{scope.change_id}"
    if scope.kind == "implement":
        if not scope.change_id:
            raise ValueError("implement scope requires change_id")
        return f"implement:{scope.change_id}"
    if scope.kind == "repair_mission":
        if not scope.mission_id:
            raise ValueError("repair_mission scope requires mission_id")
        return scope.mission_id
    if scope.kind == "bugbot_review":
        if not scope.mission_id:
            raise ValueError("bugbot_review scope requires mission_id")
        return scope.mission_id
    raise ValueError(f"unsupported workflow scope kind: {scope.kind}")


def default_run_dir(repo_root: Path, scope: WorkflowResumeScope) -> Path:
    """Каталог run для scope (без случайного суффикса)."""
    repo_root = repo_root.resolve()
    if scope.kind == "verify_impl":
        if not scope.change_id:
            raise ValueError("verify_impl scope requires change_id")
        return resource_path(repo_root, "opsx", "runs", "verify", f"impl-{scope.change_id}")
    if scope.kind == "implement":
        if not scope.change_id:
            raise ValueError("implement scope requires change_id")
        return resource_path(repo_root, "opsx", "runs", "implement", scope.change_id)
    if scope.kind == "repair_mission":
        if not scope.mission_id:
            raise ValueError("repair_mission scope requires mission_id")
        return resource_path(repo_root, "opsx", "runs", "repair", scope.mission_id)
    if scope.kind == "bugbot_review":
        if not scope.mission_id:
            raise ValueError("bugbot_review scope requires mission_id")
        return resource_path(repo_root, "opsx", "runs", "bugbot", scope.mission_id)
    raise ValueError(f"unsupported workflow scope kind: {scope.kind}")


def _legacy_verify_impl_dirs(repo_root: Path, change_id: str) -> list[Path]:
    verify_root = resource_path(repo_root, "opsx", "runs", "verify")
    if not verify_root.is_dir():
        return []
    prefix = f"impl-{change_id}-"
    legacy: list[Path] = []
    for child in verify_root.iterdir():
        if not child.is_dir():
            continue
        if not child.name.startswith(prefix):
            continue
        suffix = child.name[len(prefix) :]
        if _LEGACY_RUN_SUFFIX.fullmatch(suffix):
            legacy.append(child)
    return sorted(legacy, key=lambda path: path.stat().st_mtime, reverse=True)


def _legacy_verify_impl_thread_id(change_id: str, run_dir: Path) -> str:
    prefix = f"impl-{change_id}-"
    return run_dir.name[len(prefix) :]


def _receipt_checkpoint_hint(repo_root: Path, change_id: str) -> Path | None:
    from workflows.artifact_paths import (
        resolve_impl_verify_receipt_path,
        session_id_from_stream_log,
    )

    receipt_path = resolve_impl_verify_receipt_path(repo_root, change_id)
    if receipt_path is None:
        return None
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not receipt.get("finished_at"):
        return None
    stream_log = str(receipt.get("stream_log") or "")
    session = session_id_from_stream_log(stream_log)
    if not session:
        return None
    legacy_dir = (
        resource_path(repo_root, "opsx", "runs", "verify", f"impl-{change_id}-{session}")
    )
    checkpoint = legacy_dir / "checkpoints.sqlite"
    return checkpoint if checkpoint.is_file() else None


def list_checkpoint_candidates(
    repo_root: Path,
    scope: WorkflowResumeScope,
) -> list[tuple[Path, str]]:
    """Список пар (checkpoints.sqlite, thread_id) от канонического к legacy run."""
    repo_root = repo_root.resolve()
    candidates: list[tuple[Path, str]] = []

    canonical_dir = default_run_dir(repo_root, scope)
    canonical_ckpt = canonical_dir / "checkpoints.sqlite"
    if canonical_ckpt.is_file():
        candidates.append((canonical_ckpt, stable_thread_id(scope)))

    if scope.kind == "verify_impl" and scope.change_id:
        hinted = _receipt_checkpoint_hint(repo_root, scope.change_id)
        if hinted is not None:
            legacy_dir = hinted.parent
            thread_id = _legacy_verify_impl_thread_id(scope.change_id, legacy_dir)
            pair = (hinted, thread_id)
            if pair not in candidates:
                candidates.insert(0, pair)
        for legacy_dir in _legacy_verify_impl_dirs(repo_root, scope.change_id):
            ckpt = legacy_dir / "checkpoints.sqlite"
            if not ckpt.is_file():
                continue
            pair = (ckpt, _legacy_verify_impl_thread_id(scope.change_id, legacy_dir))
            if pair not in candidates:
                candidates.append(pair)

    return candidates


def _verify_impl_checkpoint_run_dirs(repo_root: Path, change_id: str) -> list[Path]:
    """Каталоги durable run verify_impl для ``change_id`` (канон + legacy + receipt hint)."""
    repo_root = resolve_opsx_repository_root(repo_root)
    scope = WorkflowResumeScope(kind="verify_impl", change_id=change_id)
    by_key: dict[str, Path] = {}
    canonical = default_run_dir(repo_root, scope)
    by_key[str(canonical.resolve())] = canonical
    for legacy_dir in _legacy_verify_impl_dirs(repo_root, change_id):
        by_key[str(legacy_dir.resolve())] = legacy_dir
    for ckpt_path, _thread_id in list_checkpoint_candidates(repo_root, scope):
        parent = ckpt_path.parent
        by_key[str(parent.resolve())] = parent
    return list(by_key.values())


def reset_verify_impl_checkpoints(repo_root: Path, change_id: str) -> None:
    """Сбросить LangGraph SQLite checkpoint для verify_impl scope одного change.

    Удаляет ``checkpoints.sqlite`` и companion ``-shm`` / ``-wal`` во всех run_dir,
    которые участвуют в ``list_checkpoint_candidates`` (канонический ``impl-<id>`` и
    legacy ``impl-<id>-<12hex>``). Другие change_id и smoke-run каталоги не трогает.

    Args:
        repo_root: Корень репозитория OPSX.
        change_id: OpenSpec change id.

    Side effects:
        Одна строка в stderr: список удалённых путей или ``checkpoint already absent``;
        при блокировке файла — отдельная строка с путём и ошибкой.
    """
    repo_root = resolve_opsx_repository_root(repo_root)
    removed: list[str] = []
    lock_errors: list[str] = []
    for run_dir in _verify_impl_checkpoint_run_dirs(repo_root, change_id):
        for suffix in ("", "-shm", "-wal"):
            path = run_dir / f"checkpoints.sqlite{suffix}"
            if not path.is_file():
                continue
            try:
                path.unlink()
                removed.append(str(path))
            except OSError as exc:
                lock_errors.append(f"{path}: {exc}")
    for line in lock_errors:
        print(line, file=sys.stderr)
    if removed:
        print(f"reset-sm: удалены checkpoint: {', '.join(removed)}", file=sys.stderr)
    elif not lock_errors:
        print("reset-sm: checkpoint already absent", file=sys.stderr)


def reset_row_walk_journal_repair_attempts(repo_root: Path, change_id: str) -> int:
    """Обнулить ``repair_attempts`` в ``row_walk_journal.json`` для verify_impl ``-r``.

    Checkpoint reset не трогает журнал row-walk; этот хук вызывается вместе с
    ``reset_verify_impl_checkpoints`` из ``run_verify_impl`` при ``--reset-sm``.

    Args:
        repo_root: Корень репозитория OPSX.
        change_id: OpenSpec change id.

    Returns:
        Число строк журнала, у которых счётчик попыток был сброшен.

    Side effects:
        При ненулевом результате — строка в stderr.
    """
    repo_root = resolve_opsx_repository_root(repo_root)
    cid = change_id.strip()
    if not cid:
        return 0
    from workflows.artifact_paths import scenario_machine_receipt_path

    journal_path = scenario_machine_receipt_path(repo_root, cid, "row_walk_journal.json")
    if not journal_path.is_file():
        return 0
    from agents.verify.scenario_coverage_verify.row_cycle import RowWalkJournal

    journal = RowWalkJournal(journal_path)
    reset_count = journal.reset_repair_attempts()
    if reset_count:
        print(
            f"reset-sm: обнулены repair_attempts в {reset_count} строк(ах) "
            f"({journal_path.relative_to(repo_root)})",
            file=sys.stderr,
        )
    return reset_count


def _node_progress_score(values: Mapping[str, Any], next_nodes: Sequence[str]) -> int:
    """Оценить, насколько далеко продвинулся run (больше = лучше)."""
    score = 0
    verdict = str(values.get("verdict") or "")
    if verdict:
        score += 500
    if values.get("apply_status") is not None:
        score += 200
    if values.get("commit_status") is not None or values.get("verify_finding_commit_outcome"):
        score += 150
    if values.get("quality_gate_verdict") is not None:
        score += 100
    if values.get("verify_stop_reason"):
        score += 50
    if next_nodes:
        score += 10 + len(next_nodes)
    return score


async def score_checkpoint_thread(
    graph: Any,
    *,
    thread_id: str,
) -> tuple[int, tuple[str, ...]] | None:
    """Оценить checkpoint для ``thread_id`` через ``graph.aget_state``."""
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = await graph.aget_state(config)
    if snapshot is None or not snapshot.values:
        return None
    next_nodes = tuple(snapshot.next or ())
    score = _node_progress_score(snapshot.values, next_nodes)
    return score, next_nodes


def _decode_branch_channel(channel: str) -> str | None:
    if channel == END:
        return None
    if channel.startswith(_BRANCH_TO_PREFIX):
        return channel[len(_BRANCH_TO_PREFIX) :]
    return None


def _find_branch_spec(writers: Sequence[Any]) -> BranchSpec | None:
    for writer in writers:
        if isinstance(writer, RunnableCallable):
            branch = getattr(writer.func, "__self__", None)
            if isinstance(branch, BranchSpec):
                return branch
    return None


def _static_successors_from_writers(writers: Sequence[Any]) -> tuple[str, ...]:
    destinations: list[str] = []
    for writer in writers:
        if not isinstance(writer, ChannelWrite):
            continue
        for entry in writer.writes:
            if not isinstance(entry, ChannelWriteEntry):
                continue
            decoded = _decode_branch_channel(entry.channel)
            if decoded:
                destinations.append(decoded)
    return tuple(dict.fromkeys(destinations))


async def _branch_successors(
    branch: BranchSpec,
    state: Mapping[str, Any],
) -> tuple[str, ...]:
    path = branch.path
    if hasattr(path, "ainvoke"):
        result = await path.ainvoke(dict(state))
    else:
        result = path.invoke(dict(state))
    if not isinstance(result, (list, tuple)):
        result = [result]
    destinations: list[str] = []
    for item in result:
        if isinstance(item, Send):
            if item.node != END:
                destinations.append(str(item.node))
            continue
        mapped: str | None
        if branch.ends is not None:
            mapped = branch.ends.get(item)
            if mapped is None:
                mapped = branch.ends.get(str(item))
        else:
            mapped = str(item) if item != END else None
        if mapped and mapped != END:
            destinations.append(str(mapped))
    return tuple(dict.fromkeys(destinations))


def successor_node_in_graph(graph: Any, successor: str) -> bool:
    """Проверить, что ``successor`` — узел именно этого compiled-графа (не inner subgraph)."""
    return successor in graph.nodes


def verify_subgraph_from_main_graph(graph: Any) -> Any:
    """Вернуть compiled verify-subgraph (узлы ``impl_verify``, ``static_repair``, …) из ``build_main_graph``."""
    wrapper = graph.nodes.get("verify")
    if wrapper is None or not wrapper.subgraphs:
        raise ValueError("main graph has no nested verify subgraph")
    return wrapper.subgraphs[0]


def verify_subgraph_invoke_config(
    graph: Any,
    config: Mapping[str, Any],
) -> tuple[Any, dict[str, Any]]:
    """Пара ``(verify-subgraph, sub_config)`` с checkpointer для nested ``aget_state`` / rewind."""
    subgraph = verify_subgraph_from_main_graph(graph)
    sub_config = _with_checkpointer_config(
        subgraph,
        config,
        _graph_checkpointer(graph, config),
    )
    return subgraph, sub_config


@dataclass(frozen=True)
class StaticRepairRewindPlan:
    """Time-travel resume verify-subgraph на checkpoint с ``next=static_repair``."""

    target_graph: Any
    config: dict[str, Any]
    checkpoint_id: str


def parent_command_goto_is_valid(graph: Any, successor: str) -> bool:
    """Можно ли безопасно послать ``Command(goto=successor)`` на этот ``graph``."""
    if not successor_node_in_graph(graph, successor):
        return False
    wrapper = graph.nodes.get(successor)
    if wrapper is not None and wrapper.subgraphs:
        return False
    return True


async def current_graph_successors_after_node(
    graph: Any,
    *,
    finished_node: str,
    state: Mapping[str, Any],
) -> tuple[str, ...]:
    """Узлы-преемники текущего графа после ``finished_node`` для сохранённого ``state``."""
    node = graph.nodes.get(finished_node)
    if node is None:
        return ()
    branch = _find_branch_spec(node.flat_writers)
    if branch is not None:
        return await _branch_successors(branch, state)
    return _static_successors_from_writers(node.flat_writers)


async def _infer_checkpoint_finished_node(graph: Any, config: Mapping[str, Any]) -> str | None:
    infer = getattr(graph, "_infer_as_node", None)
    checkpointer = getattr(graph, "checkpointer", None)
    if infer is None or checkpointer is None:
        return None
    saved = await checkpointer.aget_tuple(dict(config))
    if saved is None:
        return None
    finished_node = infer(saved.checkpoint.get("versions_seen") or {})
    if not finished_node or finished_node not in graph.nodes:
        return None
    return str(finished_node)


def _coverage_activation_pipeline_gap(state: Mapping[str, Any]) -> bool:
    """True, если coverage activation прошёл, а quality gates ещё не запускались."""
    if str(state.get("quality_gates_execution_status") or "").strip():
        return False
    stop = str(state.get("verify_stop_reason") or "").strip()
    if stop == "Coverage Activation":
        return True
    ca_verdict = str(state.get("coverage_activation_verdict") or "").strip()
    return bool(ca_verdict and stop in ("", "Coverage Activation"))


def stale_successor_after_coverage_activation_gap(
    graph: Any,
    state: Mapping[str, Any],
) -> str | None:
    """Устаревший END после coverage activation без quality gates → ``static_repair``.

    Старые checkpoint'ы останавливали subgraph на ``coverage_activation_terminal``
    с ``verify_stop_reason=Coverage Activation``; текущий граф всегда ведёт в gate_repair.
    """
    if "static_repair" not in graph.nodes:
        return None
    if not _coverage_activation_pipeline_gap(state):
        return None
    return "static_repair"


def stale_coverage_activation_resume_state_patch(state: Mapping[str, Any]) -> dict[str, Any]:
    """Сбросить terminal stop-поля перед ``Command(goto=static_repair)`` после coverage activation."""
    if not _coverage_activation_pipeline_gap(state):
        return {}
    return {"verify_stop_reason": "", "owner_attention": ""}


def _hop_answer_status_key_for_stage(stage: str) -> str | None:
    """Ключ typed hop status в checkpoint (после успешного SDK agent)."""
    from workflows.implementation.routing_contract import (
        STAGE_APPLY_GREEN,
        STAGE_APPLY_IMPLEMENT,
        STAGE_APPLY_RED,
        STAGE_APPLY_SCAFFOLD,
    )

    mapping = {
        STAGE_APPLY_SCAFFOLD: "apply_scaffold_status",
        STAGE_APPLY_RED: "apply_red_status",
        STAGE_APPLY_IMPLEMENT: "apply_implement_status",
        STAGE_APPLY_GREEN: "apply_green_status",
    }
    return mapping.get(stage)


def _stale_resume_execute_hop_completed(
    snapshot_values: Mapping[str, Any],
    *,
    stage: str,
) -> bool:
    """True, если mutating SDK hop для ``stage`` реально отработал (не pre-agent PAUSE)."""
    from workflows.implementation.adapter_hop_pause import PRE_AGENT_EXECUTE_PAUSE_CODES

    recovery = str(snapshot_values.get("implementation_recovery_code") or "")
    if recovery in PRE_AGENT_EXECUTE_PAUSE_CODES:
        return False
    status_key = _hop_answer_status_key_for_stage(stage)
    if status_key and str(snapshot_values.get(status_key) or "").strip():
        return True
    run_id = str(snapshot_values.get("run_id") or "").strip()
    if run_id and recovery not in {
        "SDK_ACTIVE_RUN_CONFLICT",
        "SDK_BRIDGE_TIMEOUT",
        "DISPATCH_LEDGER_ACTIVE_HOP",
    }:
        return True
    last_hop = str(snapshot_values.get("implementation_last_hop") or "")
    if last_hop == stage and recovery == "DISPATCH_UNCHANGED_AFTER_HOP":
        return True
    return False


def _stale_successor_skip_plan_hop_to_execute(
    graph: Any,
    *,
    finished_node: str | None,
    snapshot_values: Mapping[str, Any],
    planned_successor: str | None,
    lane_snapshot_values: Mapping[str, Any] | None = None,
) -> str | None:
    """Plan JSON RUNNING вперёд: stale resume в execute, не снова ``implementation_route``.

    ``route_after_implementation_execute`` и design→``implementation_route`` ведут в plan
    hop; при stale END checkpoint ещё хранит runtime PAUSE (stagnation), а SoT plan уже на
    forward stage — повтор ``opsx-super-implement.mjs plan`` зацикливает до
    ``_MAX_VERIFY_PIPELINE_CONTINUATIONS``.

    Pre-agent PAUSE (``STAGE_NOT_WIRED``, dispatch gate без SDK) не считается завершённым hop.
    """
    from workflows.implementation.routing import implementation_code_entry_node_id

    _legacy_plan_route_node = "implementation_route"
    from workflows.implementation.routing_contract import EXECUTABLE_STAGES, RUN_STATUS_RUNNING

    lane_view = lane_snapshot_values if lane_snapshot_values is not None else snapshot_values
    forward_running = (
        str(lane_view.get("implementation_status") or "").strip() == RUN_STATUS_RUNNING
        and str(lane_view.get("implementation_stage") or "").strip() in EXECUTABLE_STAGES
    )
    if not _lane_terminal_superseded_by_super_implement_plan(lane_view) and not forward_running:
        return None
    if planned_successor != _legacy_plan_route_node:
        return None
    code_entry = implementation_code_entry_node_id(graph)
    if code_entry is None or not successor_node_in_graph(graph, code_entry):
        return None
    if finished_node == "apply_design_lane":
        return code_entry
    allowed_finished = {
        "implementation_execute_hop",
        "apply_code_lane",
        _legacy_plan_route_node,
    }
    if finished_node not in allowed_finished:
        return None
    stage = str(snapshot_values.get("implementation_stage") or "")
    if _stale_resume_execute_hop_completed(snapshot_values, stage=stage):
        return None
    return code_entry


def _stale_implementation_prefer_execute_over_plan_route(
    lane_snapshot: Mapping[str, Any],
    routing_state: Mapping[str, Any],
    successor: str | None,
) -> str | None:
    """Не stale-goto в plan hop, пока forward stage ещё не имел SDK execute."""
    _legacy_plan_route_node = "implementation_route"
    if successor != _legacy_plan_route_node:
        return successor
    if not _lane_terminal_superseded_by_super_implement_plan(lane_snapshot):
        return successor
    resume_view = {**dict(lane_snapshot), **dict(routing_state)}
    stage = str(resume_view.get("implementation_stage") or "").strip()
    if not stage:
        return successor
    if _stale_resume_execute_hop_completed(resume_view, stage=stage):
        return successor
    return "implementation_execute_hop"


async def _resolve_stale_end_on_snapshot(
    graph: Any,
    config: Mapping[str, Any],
    snapshot: Any,
    state: Mapping[str, Any],
) -> str | None:
    stale_ca = stale_successor_after_coverage_activation_gap(graph, state)
    if stale_ca is not None and successor_node_in_graph(graph, stale_ca):
        return stale_ca
    finished_node = await _infer_checkpoint_finished_node(graph, config)
    if finished_node is None and "impl_verify" in graph.nodes:
        if str(state.get("verdict") or "") and not snapshot.next:
            finished_node = "impl_verify"
    if finished_node is None:
        return None
    successors = await current_graph_successors_after_node(
        graph,
        finished_node=finished_node,
        state=state,
    )
    if not successors:
        return None
    successor = successors[0]
    _legacy_plan_route_node = "implementation_route"
    if (
        successor != _legacy_plan_route_node
        and not successor_node_in_graph(graph, successor)
    ):
        return None
    resume_view = {**dict(snapshot.values), **dict(state)}
    stale_exec = _stale_successor_skip_plan_hop_to_execute(
        graph,
        finished_node=finished_node,
        snapshot_values=resume_view,
        planned_successor=successor,
        lane_snapshot_values=snapshot.values,
    )
    if stale_exec is not None:
        return normalize_stale_apply_successor(graph, stale_exec)
    coerced = await _coerce_implementation_stale_successor_to_execute(
        graph,
        config,
        successor,
        snapshot.values,
    )
    return normalize_stale_apply_successor(graph, coerced)


async def _coerce_implementation_stale_successor_to_execute(
    graph: Any,
    config: Mapping[str, Any],
    successor: str,
    snapshot_values: Mapping[str, Any],
) -> str:
    """Не stale-resume в plan hop, если SoT plan уже ведёт в forward execute."""
    _legacy_plan_route_node = "implementation_route"
    if successor != _legacy_plan_route_node:
        return successor
    finished_node = await _infer_checkpoint_finished_node(graph, config)
    resume_view = {
        **dict(snapshot_values),
        **_routing_state_for_stale_implementation_resume(snapshot_values),
    }
    alt = _stale_successor_skip_plan_hop_to_execute(
        graph,
        finished_node=finished_node,
        snapshot_values=resume_view,
        planned_successor=successor,
        lane_snapshot_values=snapshot_values,
    )
    return alt or successor


async def _plan_stale_end_in_subgraph_wrappers(
    graph: Any,
    config_dict: Mapping[str, Any],
    *,
    finished_node: str | None,
) -> StaleEndResumePlan | None:
    """Найти stale END в nested subgraph и вернуть ``Command(goto=...)`` для него."""
    checkpointer = _graph_checkpointer(graph, config_dict)
    if checkpointer is None:
        return None

    wrapper_nodes: list[tuple[str, Any]] = []
    if finished_node and finished_node in graph.nodes:
        wrapper = graph.nodes[finished_node]
        if wrapper.subgraphs:
            wrapper_nodes.append((finished_node, wrapper.subgraphs[0]))
    if not wrapper_nodes:
        for node_name, wrapper in graph.nodes.items():
            if wrapper.subgraphs:
                wrapper_nodes.append((node_name, wrapper.subgraphs[0]))

    seen_subgraphs: set[int] = set()
    for node_name, subgraph in wrapper_nodes:
        sub_id = id(subgraph)
        if sub_id in seen_subgraphs:
            continue
        seen_subgraphs.add(sub_id)

        prefix = f"{node_name}:"
        sub_config = _with_checkpointer_config(
            subgraph,
            config_dict,
            checkpointer,
        )
        sub_snapshot = await subgraph.aget_state(sub_config)
        if sub_snapshot.values and not sub_snapshot.next:
            if _terminal_implementation_lane_pause(sub_snapshot.values):
                logger.info(
                    "checkpoint plan_stale_end refused: implementation subgraph "
                    "at terminal PAUSE/FAILED wrapper=%s",
                    node_name,
                )
                return None
            sub_routing = _routing_state_for_stale_implementation_resume(sub_snapshot.values)
            sub_successor = await _resolve_stale_end_on_snapshot(
                subgraph,
                sub_config,
                sub_snapshot,
                sub_routing,
            )
            sub_successor = _stale_implementation_prefer_execute_over_plan_route(
                sub_snapshot.values,
                sub_routing,
                sub_successor,
            )
            if sub_successor is not None:
                return StaleEndResumePlan(subgraph, sub_config, sub_successor)

        async for checkpoint_tuple in checkpointer.alist(config_dict, limit=64):
            ns = checkpoint_tuple.config.get("configurable", {}).get("checkpoint_ns", "")
            if not ns.startswith(prefix):
                continue
            sub_config = _with_checkpointer_config(
                subgraph,
                checkpoint_tuple.config,
                checkpointer,
            )
            sub_snapshot = await subgraph.aget_state(sub_config)
            if sub_snapshot.next or not sub_snapshot.values:
                continue
            sub_state = sub_snapshot.values
            sub_routing_hist = _routing_state_for_stale_implementation_resume(sub_state)
            sub_successor = await _resolve_stale_end_on_snapshot(
                subgraph,
                sub_config,
                sub_snapshot,
                sub_routing_hist,
            )
            sub_successor = _stale_implementation_prefer_execute_over_plan_route(
                sub_state,
                sub_routing_hist,
                sub_successor,
            )
            if sub_successor is not None:
                return StaleEndResumePlan(subgraph, sub_config, sub_successor)
    return None


def _subgraph_for_checkpoint_ns(graph: Any, checkpoint_ns: str) -> Any | None:
    """Compiled subgraph, соответствующий ``checkpoint_ns`` (``node:uuid|…``)."""
    if not checkpoint_ns:
        return graph
    current = graph
    for segment in checkpoint_ns.split("|"):
        node_name = segment.split(":", 1)[0]
        wrapper = current.nodes.get(node_name)
        if wrapper is None or not wrapper.subgraphs:
            return None
        current = wrapper.subgraphs[0]
    return current


def _pending_checkpoint_score(
    values: Mapping[str, Any],
    next_nodes: Sequence[str],
) -> int:
    """Оценка приоритета resume: выше — «последний реальный pending hop»."""
    if not next_nodes:
        return -1
    score = 10 + len(next_nodes) * 5
    first = str(next_nodes[0])
    if first == "diagnostic":
        score += 2000
    elif first in ("repair_order", "group_error", "dispatch_cycle", "verify_after_group_pass"):
        score += 1500
    elif first == "static_repair":
        score += 1200
    elif first in ("apply", "verify_apply_commit", "prepare_impl_restart"):
        score += 1100
    elif first == "impl_verify":
        score += 100
        if not str(values.get("verdict") or "").strip():
            score -= 80
    if str(values.get("verdict") or "") == "IMPL_OK":
        score += 300
    if values.get("repair_order_result") or values.get("repair_order"):
        score += 400
    return score


def _nested_pending_worthy(
    values: Mapping[str, Any],
    next_nodes: Sequence[str],
) -> bool:
    """True, если nested ``next`` — реальная точка обрыва, а не свежий ``impl_verify``."""
    score = _pending_checkpoint_score(values, next_nodes)
    if score < 0:
        return False
    first = str(next_nodes[0]) if next_nodes else ""
    if first == "impl_verify":
        return score >= 500
    return score >= 1100 or first in (
        "diagnostic",
        "repair_order",
        "group_error",
        "dispatch_cycle",
        "verify_after_group_pass",
        "static_repair",
    )


def _checkpoint_ns_from_config(config: Mapping[str, Any]) -> str:
    """Вернуть ``checkpoint_ns`` из LangGraph configurable config."""
    configurable = config.get("configurable") or {}
    return str(configurable.get("checkpoint_ns") or "")


async def _consider_pending_subgraph(
    subgraph: Any,
    sub_config: Mapping[str, Any],
    *,
    checkpointer: Any | None,
    values: Mapping[str, Any],
    next_nodes: Sequence[str],
    best: list[tuple[int, Any, dict[str, Any], tuple[str, ...]]],
) -> None:
    score = _pending_checkpoint_score(values, next_nodes)
    if score < 0:
        return
    config_dict = _with_checkpointer_config(subgraph, sub_config, checkpointer)
    next_tuple = tuple(next_nodes)
    if best and score <= best[0][0]:
        return
    best[:] = [(score, subgraph, config_dict, next_tuple)]


async def _nested_task_state_snapshot(
    graph: Any,
    task: Any,
    *,
    checkpointer: Any | None,
) -> StateSnapshot | None:
    """Вернуть ``StateSnapshot`` задачи subgraph из ``aget_state(..., subgraphs=True)``."""
    nested = task.state
    if nested is None:
        return None
    if hasattr(nested, "next") and hasattr(nested, "config") and hasattr(nested, "values"):
        return nested  # type: ignore[return-value]
    if isinstance(nested, Mapping):
        ns = _checkpoint_ns_from_config(nested)
        target = _subgraph_for_checkpoint_ns(graph, ns)
        if target is None:
            return None
        sub_config = _with_checkpointer_config(target, nested, checkpointer)
        return await target.aget_state(sub_config, subgraphs=True)
    return None


async def _collect_pending_from_snapshot_tasks(
    graph: Any,
    tasks: Sequence[Any],
    *,
    checkpointer: Any | None,
    best: list[tuple[int, Any, dict[str, Any], tuple[str, ...]]],
) -> None:
    """Собрать pending resume targets из ``StateSnapshot.tasks`` (без ``alist``)."""
    for task in tasks:
        nested = await _nested_task_state_snapshot(
            graph,
            task,
            checkpointer=checkpointer,
        )
        if nested is None:
            continue
        next_nodes = tuple(nested.next or ())
        if next_nodes:
            ns = _checkpoint_ns_from_config(nested.config)
            target = _subgraph_for_checkpoint_ns(graph, ns)
            if target is not None:
                await _consider_pending_subgraph(
                    target,
                    nested.config,
                    checkpointer=checkpointer,
                    values=nested.values or {},
                    next_nodes=next_nodes,
                    best=best,
                )
        if nested.tasks:
            await _collect_pending_from_snapshot_tasks(
                graph,
                nested.tasks,
                checkpointer=checkpointer,
                best=best,
            )


async def _resolve_nested_pending_subgraph(
    graph: Any,
    config: Mapping[str, Any],
) -> tuple[Any, dict[str, Any], tuple[str, ...]] | None:
    """Найти nested subgraph с незавершённым ``snapshot.next`` для ``ainvoke(None)``.

    Использует нативный ``graph.aget_state(config, subgraphs=True)``: LangGraph
    рекурсивно подгружает ``task.state`` для subgraph-задач без перечисления истории
    через ``checkpointer.alist``.

    Возвращает ``(subgraph, sub_config, next_nodes)``. Resume выполняется на subgraph
    с ``checkpoint_ns`` из ``sub_config``, как при static_repair rewind.

    Среди всех pending task-state (включая ``static_repair`` → ``diagnostic``) побеждает
    target с максимальным ``_pending_checkpoint_score``, чтобы свежий ``impl_verify`` не
    затенял более глубокий pending hop.
    """
    config_dict = _with_checkpointer_config(graph, config)
    checkpointer = _graph_checkpointer(graph, config_dict)
    root_snapshot = await graph.aget_state(config_dict, subgraphs=True)
    thread_id = _thread_id_from_config(config_dict)
    logger.debug(
        "checkpoint path=aget_state subgraphs=True thread_id=%s %s %s",
        thread_id,
        format_checkpoint_snapshot_summary(root_snapshot, config_dict),
        _format_tasks_summary(tuple(root_snapshot.tasks or ())),
    )
    best: list[tuple[int, Any, dict[str, Any], tuple[str, ...]]] = []
    await _collect_pending_from_snapshot_tasks(
        graph,
        tuple(root_snapshot.tasks or ()),
        checkpointer=checkpointer,
        best=best,
    )
    if not best:
        logger.debug(
            "checkpoint path=aget_state_subgraphs result=empty "
            "thread_id=%s reason=no nested task with score>=0 in snapshot.tasks",
            thread_id,
        )
        return None
    score, subgraph, sub_config, next_nodes = best[0]
    logger.debug(
        "checkpoint path=aget_state_subgraphs result=candidate thread_id=%s score=%s next=%s ns=%s",
        thread_id,
        score,
        next_nodes,
        _checkpoint_ns_from_config(sub_config) or "-",
    )
    return subgraph, sub_config, next_nodes


def _next_matches_pending(live_next: Sequence[str], pending_next: Sequence[str]) -> bool:
    """True, если снимок подграфа ещё ждёт тот же hop, что и pending-задача."""
    if not live_next:
        return False
    if not pending_next:
        return True
    return any(node in live_next for node in pending_next)


_STATIC_REPAIR_INTERIOR_HOPS = frozenset(
    {
        "diagnostic",
        "repair_order",
        "group_error",
        "dispatch_cycle",
        "verify_after_group_pass",
    }
)


def _is_static_repair_interior_hop(next_nodes: Sequence[str]) -> bool:
    """True, если hop выполняется внутри compiled ``static_repair`` subgraph."""
    return any(str(node) in _STATIC_REPAIR_INTERIOR_HOPS for node in next_nodes)


# Узел gate_repair, на котором ``ainvoke(None)`` реально выполняет hop (не subgraph-wrapper).
_GATE_REPAIR_RUNNABLE_PREDECESSOR: dict[str, str] = {
    "dispatch_cycle": "diagnostic",
    "diagnostic": "repair_order",
    "repair_order": "group_error",
    "verify_after_group_pass": "dispatch_cycle",
}


def _checkpoint_ns_contains_hop(checkpoint_ns: str, hop: str) -> bool:
    """True, если ``checkpoint_ns`` содержит сегмент compiled-узла ``hop:``."""
    if not checkpoint_ns or not hop:
        return False
    needle = f"{hop}:"
    return checkpoint_ns.startswith(needle) or f"|{needle}" in checkpoint_ns


async def _walk_snapshot_tasks_for_wrapper_nested_pending(
    graph: Any,
    tasks: Sequence[Any],
    *,
    checkpointer: Any | None,
    wrapper_hop: str,
) -> tuple[Any, dict[str, Any], tuple[str, ...]] | None:
    """Найти live ``next`` внутри subgraph-wrapper ``wrapper_hop`` по ``StateSnapshot.tasks``."""
    for task in tasks:
        nested = await _nested_task_state_snapshot(
            graph,
            task,
            checkpointer=checkpointer,
        )
        if nested is None:
            continue
        ns = _checkpoint_ns_from_config(nested.config)
        nested_next = tuple(nested.next or ())
        if nested_next and _checkpoint_ns_contains_hop(ns, wrapper_hop):
            target = _subgraph_for_checkpoint_ns(graph, ns)
            if target is not None:
                cfg = _with_checkpointer_config(target, nested.config, checkpointer)
                return target, cfg, nested_next
        if nested.tasks:
            found = await _walk_snapshot_tasks_for_wrapper_nested_pending(
                graph,
                nested.tasks,
                checkpointer=checkpointer,
                wrapper_hop=wrapper_hop,
            )
            if found is not None:
                return found
    return None


async def find_wrapper_hop_nested_resume(
    main_graph: Any,
    owner_graph: Any,
    invoke_config: Mapping[str, Any],
    pending_next: Sequence[str],
) -> tuple[Any, dict[str, Any], tuple[str, ...]] | None:
    """Resume на вложенном графе под compiled-узлом (``dispatch_cycle`` в gate_repair).

    Когда у owner ``next=('dispatch_cycle',)``, а ``owner.ainvoke(None)`` — no-op,
    pending hop часто живёт во вложенном ``dispatch_cycle`` (``static_triage``, …).

    Returns:
        ``(inner_graph, inner_config, inner_next)`` или ``None``.
    """
    if not pending_next:
        return None
    wrapper_hop = str(pending_next[0])
    wrapper = owner_graph.nodes.get(wrapper_hop)
    if wrapper is None or not wrapper.subgraphs:
        return None
    config_dict = _with_checkpointer_config(owner_graph, invoke_config)
    checkpointer = _graph_checkpointer(main_graph, config_dict)
    owner_snap = await owner_graph.aget_state(config_dict, subgraphs=True)
    if owner_snap and owner_snap.tasks:
        found = await _walk_snapshot_tasks_for_wrapper_nested_pending(
            main_graph,
            tuple(owner_snap.tasks),
            checkpointer=checkpointer,
            wrapper_hop=wrapper_hop,
        )
        if found is not None:
            return found
    inner_graph = wrapper.subgraphs[0]
    inner_cfg = _with_checkpointer_config(inner_graph, invoke_config, checkpointer)
    inner_snap = await inner_graph.aget_state(inner_cfg, subgraphs=True)
    if inner_snap is not None:
        inner_next = tuple(inner_snap.next or ())
        if inner_next:
            return inner_graph, inner_cfg, inner_next
        if inner_snap.tasks:
            found = await _walk_snapshot_tasks_for_wrapper_nested_pending(
                main_graph,
                tuple(inner_snap.tasks),
                checkpointer=checkpointer,
                wrapper_hop=wrapper_hop,
            )
            if found is not None:
                return found

    if checkpointer is not None and hasattr(checkpointer, "alist"):
        base_ns = _checkpoint_ns_from_config(invoke_config)
        needle = f"|{wrapper_hop}:"
        config_dict = _with_checkpointer_config(main_graph, invoke_config, checkpointer)
        async for checkpoint_tuple in checkpointer.alist(config_dict, limit=64):
            tuple_config = checkpoint_tuple.config
            if not tuple_config:
                continue
            ns = _checkpoint_ns_from_config(tuple_config)
            if not ns or wrapper_hop not in ns:
                continue
            if base_ns and not (ns.startswith(base_ns) or ns.startswith(f"{base_ns}{needle}")):
                if needle not in ns and not ns.endswith(f"{wrapper_hop}:"):
                    continue
            hist_cfg = _with_checkpointer_config(inner_graph, tuple_config, checkpointer)
            hist_snap = await inner_graph.aget_state(hist_cfg, subgraphs=True)
            if hist_snap is None:
                continue
            hist_next = tuple(hist_snap.next or ())
            if hist_next:
                return inner_graph, hist_cfg, hist_next
            if hist_snap.tasks:
                found = await _walk_snapshot_tasks_for_wrapper_nested_pending(
                    main_graph,
                    tuple(hist_snap.tasks),
                    checkpointer=checkpointer,
                    wrapper_hop=wrapper_hop,
                )
                if found is not None:
                    return found
    return None


async def _find_runnable_predecessor_rewind_plan(
    owner_graph: Any,
    invoke_config: Mapping[str, Any],
    stuck_hop: str,
) -> tuple[dict[str, Any], tuple[str, ...], str] | None:
    """Checkpoint gate_repair, у которого ``next`` — runnable predecessor застрявшего hop."""
    predecessor = _GATE_REPAIR_RUNNABLE_PREDECESSOR.get(stuck_hop)
    if predecessor is None:
        return None
    config_dict = _with_checkpointer_config(owner_graph, invoke_config)
    current_id = str((invoke_config.get("configurable") or {}).get("checkpoint_id") or "")
    history_fn = getattr(owner_graph, "aget_state_history", None)
    if history_fn is None:
        return None
    history = history_fn(config_dict)
    async for hist in history:
        hist_config = hist.config
        if not hist_config:
            continue
        ckpt_id = str((hist_config.get("configurable") or {}).get("checkpoint_id") or "")
        if ckpt_id == current_id:
            continue
        hist_next = tuple(hist.next or ())
        if _next_matches_pending(hist_next, (predecessor,)):
            return dict(hist_config), hist_next, predecessor
    return None


def _is_root_checkpoint_config(config: Mapping[str, Any]) -> bool:
    """True, если ``config`` указывает на корневой checkpoint thread (без ``checkpoint_ns``)."""
    return not _checkpoint_ns_from_config(config).strip()


def _graph_invoke_label(graph: Any) -> str:
    """Короткое имя compiled graph для логов ``checkpoint ainvoke``."""
    name = getattr(graph, "name", None)
    if isinstance(name, str) and name.strip():
        return name
    return type(graph).__name__


async def _effective_pending_next_at_config(
    graph: Any,
    config: Mapping[str, Any],
    *,
    subgraphs: bool,
) -> tuple[str, ...]:
    """Собрать ``next`` для сравнения до/после resume (root ``next`` или live nested hop)."""
    config_dict = _with_checkpointer_config(graph, config)
    snapshot = await graph.aget_state(config_dict, subgraphs=subgraphs)
    root_next = tuple(snapshot.next or ()) if snapshot else ()
    if root_next:
        return root_next
    nested = await _resolve_nested_pending_subgraph(graph, config_dict)
    if nested is None:
        return ()
    sub, sub_cfg, pending_next = nested
    sub_snap = await sub.aget_state(sub_cfg)
    live_next = tuple(sub_snap.next or ()) if sub_snap else ()
    if live_next:
        return live_next
    return tuple(pending_next)


async def _invoke_config_has_actionable_pending(
    graph: Any,
    config: Mapping[str, Any],
    pending_next: Sequence[str],
) -> bool:
    """True, если ``graph.ainvoke(None, config)`` не no-op: есть свой ``next`` или live nested hop."""
    config_dict = _with_checkpointer_config(graph, config)
    snapshot = await graph.aget_state(config_dict, subgraphs=True)
    if snapshot is None:
        return False
    root_next = tuple(snapshot.next or ())
    if _next_matches_pending(root_next, pending_next):
        return True
    nested = await _resolve_nested_pending_subgraph(graph, config_dict)
    if nested is None:
        return False
    sub, sub_cfg, nxt = nested
    if not _next_matches_pending(nxt, pending_next):
        return False
    sub_snap = await sub.aget_state(sub_cfg)
    live_next = tuple(sub_snap.next or ()) if sub_snap else ()
    return bool(live_next)


async def _find_verify_parent_static_repair_resume(
    graph: Any,
    anchor_config: Mapping[str, Any],
    pending_next: Sequence[str],
) -> tuple[Any, dict[str, Any], tuple[str, ...]] | None:
    """Найти verify-subgraph checkpoint с ``next=static_repair`` и live nested hop.

    Используется, когда ``static_repair.ainvoke`` с pinned ``checkpoint_ns`` no-op,
    но verify ещё может продолжить вложенную задачу без повторного ``impl_verify``.

    Returns:
        ``(verify_subgraph, hist.config, live_nested_next)`` или ``None``.
    """
    if "verify" not in graph.nodes:
        return None
    try:
        verify_subgraph, sub_config = verify_subgraph_invoke_config(graph, anchor_config)
    except ValueError:
        return None
    history = verify_subgraph.aget_state_history(sub_config)
    async for hist in history:
        hist_next = tuple(hist.next or ())
        if "static_repair" not in hist_next:
            continue
        hist_config = hist.config
        if not hist_config:
            continue
        nested = await _resolve_nested_pending_subgraph(graph, hist_config)
        if nested is None:
            continue
        nested_sub, nested_cfg, nested_next = nested
        if not _next_matches_pending(nested_next, pending_next):
            continue
        nested_snap = await nested_sub.aget_state(nested_cfg)
        live_next = tuple(nested_snap.next or ()) if nested_snap else ()
        if not live_next or not _next_matches_pending(live_next, pending_next):
            continue
        ckpt_id = str((hist_config.get("configurable") or {}).get("checkpoint_id") or "")
        if not ckpt_id:
            continue
        return verify_subgraph, dict(hist_config), live_next
    return None


async def _ainvoke_would_restart_impl_verify(
    main_graph: Any,
    target_graph: Any,
    invoke_config: Mapping[str, Any],
    pending_next: Sequence[str],
) -> bool:
    """True, если ``ainvoke`` на ``target_graph`` снова зайдёт в ``verify_cycle_gate`` / ``impl_verify``."""
    if not _is_static_repair_interior_hop(pending_next):
        return False
    if target_graph is main_graph and _is_root_checkpoint_config(invoke_config):
        return True
    snap = await target_graph.aget_state(dict(invoke_config))
    own_next = tuple(snap.next or ()) if snap else ()
    if _next_matches_pending(own_next, pending_next):
        return False
    if "verify" in main_graph.nodes:
        try:
            verify_subgraph = verify_subgraph_from_main_graph(main_graph)
        except ValueError:
            verify_subgraph = None
        if verify_subgraph is not None and target_graph is verify_subgraph:
            if "static_repair" in own_next:
                return False
    if target_graph is main_graph:
        return True
    return not bool(own_next)


async def _resolve_history_bookmark_invoke_target(
    graph: Any,
    anchor_config: Mapping[str, Any],
    pending_subgraph: Any,
    pinned: tuple[dict[str, Any], tuple[str, ...], Mapping[str, Any]],
) -> tuple[Any, dict[str, Any], tuple[str, ...]] | None:
    """Выбрать граф и config, у которых **собственный** ``aget_state().next`` содержит hop обрыва.

    Для ``dispatch_cycle`` / ``diagnostic`` внутри ``static_repair`` **не** используется корневой
    checkpoint thread: root ``ainvoke`` снова входит в ``verify_cycle_gate`` → ``impl_verify``.
    Сначала pinned ``static_repair`` snapshot с ``checkpoint_ns``; если его ``next`` пуст —
    verify history с ``next=static_repair`` и live nested hop; иначе отказ.

    Returns:
        ``(target_graph, invoke_config, own_next)`` для ``target_graph.ainvoke(None, config=invoke_config)``.
    """
    resume_config, resume_next, _values = pinned
    if not resume_next:
        return None

    owner_cfg = _with_checkpointer_config(pending_subgraph, resume_config)
    owner_snap = await pending_subgraph.aget_state(owner_cfg)
    owner_next = tuple(owner_snap.next or ()) if owner_snap else ()
    if _next_matches_pending(owner_next, resume_next):
        if not await _ainvoke_would_restart_impl_verify(
            graph,
            pending_subgraph,
            resume_config,
            resume_next,
        ):
            return pending_subgraph, dict(resume_config), owner_next

    if _is_static_repair_interior_hop(resume_next):
        verify_parent = await _find_verify_parent_static_repair_resume(
            graph,
            anchor_config,
            resume_next,
        )
        if verify_parent is not None:
            return verify_parent
        logger.debug("checkpoint resume refused: would restart impl_verify")
        return None

    if _next_matches_pending(owner_next, resume_next):
        return pending_subgraph, dict(resume_config), owner_next

    if (
        not _is_root_checkpoint_config(resume_config)
        and await _invoke_config_has_actionable_pending(graph, anchor_config, resume_next)
    ):
        nested = await _resolve_nested_pending_subgraph(
            graph,
            _with_checkpointer_config(graph, anchor_config),
        )
        if nested is not None:
            sub, sub_cfg, _nxt = nested
            sub_snap = await sub.aget_state(sub_cfg)
            live = tuple(sub_snap.next or ()) if sub_snap else ()
            if live:
                return sub, dict(sub_cfg), live

    return None


async def _checkpoint_ainvoke_resume(
    target_graph: Any,
    invoke_config: dict[str, Any],
    *,
    use_subgraphs: bool,
) -> bool:
    """Выполнить ``ainvoke(None)`` с логами; ``False``, если durable ``next`` не изменился (no-op)."""
    pre_snap = await target_graph.aget_state(invoke_config)
    pre_own_next = tuple(pre_snap.next or ()) if pre_snap else ()
    pre_next = pre_own_next or await _effective_pending_next_at_config(
        target_graph,
        invoke_config,
        subgraphs=use_subgraphs,
    )
    configurable = invoke_config.get("configurable") or {}

    async def _measure_post_next() -> tuple[str, ...]:
        post_snap = await target_graph.aget_state(invoke_config)
        post_own_next = tuple(post_snap.next or ()) if post_snap else ()
        return post_own_next or await _effective_pending_next_at_config(
            target_graph,
            invoke_config,
            subgraphs=use_subgraphs,
        )

    async def _ainvoke_payload(payload: Command | GraphState | None) -> tuple[str, ...]:
        logger.debug(
            "checkpoint ainvoke graph=%s checkpoint_id=%s checkpoint_ns=%s snapshot.next=%s",
            _graph_invoke_label(target_graph),
            str(configurable.get("checkpoint_id") or "-"),
            _checkpoint_ns_from_config(invoke_config) or "-",
            pre_own_next,
        )
        await target_graph.ainvoke(
            payload,
            config=invoke_config,
            durability="sync",
        )
        post_next = await _measure_post_next()
        logger.debug("checkpoint ainvoke returned next=%s", post_next)
        return post_next

    post_next = await _ainvoke_payload(None)
    if pre_next != post_next:
        return True

    logger.debug(
        "checkpoint ainvoke no progress graph=%s checkpoint_id=%s next=%s",
        _graph_invoke_label(target_graph),
        str(configurable.get("checkpoint_id") or "-"),
        post_next,
    )
    if len(pre_own_next) == 1:
        hop = str(pre_own_next[0])
        wrapper = target_graph.nodes.get(hop)
        if wrapper is not None and wrapper.subgraphs:
            logger.debug("checkpoint ainvoke retry Command(goto=%s)", hop)
            post_next = await _ainvoke_payload(Command(goto=hop))
            if pre_next != post_next:
                return True
            logger.debug(
                "checkpoint ainvoke no progress graph=%s checkpoint_id=%s next=%s",
                _graph_invoke_label(target_graph),
                str(configurable.get("checkpoint_id") or "-"),
                post_next,
            )
    return False


async def _subgraph_checkpoint_still_pending(
    subgraph: Any,
    sub_config: Mapping[str, Any],
    pending_next: Sequence[str],
) -> tuple[dict[str, Any], tuple[str, ...], Mapping[str, Any]] | None:
    """Вернуть config подграфа, у которого ``next`` ещё содержит hop обрыва.

    ``pending_next`` с родительской истории может указывать на ``dispatch_cycle``,
    пока ``aget_state(sub_config).next`` уже пуст: ``checkpoint_id`` тогда принадлежит
    родителю, и ``ainvoke(None)`` сразу выходит. В этом случае берётся снимок из
    ``subgraph.aget_state_history``, у которого ``next`` совпадает с hop.

    Returns:
        ``(config, next, values)`` или ``None``, если такого снимка нет.
    """
    snapshot = await subgraph.aget_state(sub_config)
    live_next = tuple(snapshot.next or ()) if snapshot else ()
    values: Mapping[str, Any] = snapshot.values if snapshot and snapshot.values else {}
    if _next_matches_pending(live_next, pending_next):
        return dict(sub_config), live_next, values
    history_fn = getattr(subgraph, "aget_state_history", None)
    if history_fn is None:
        return None
    history = history_fn(sub_config)
    async for hist in history:
        hist_next = tuple(hist.next or ())
        if not _next_matches_pending(hist_next, pending_next):
            continue
        hist_config = hist.config
        if not hist_config:
            continue
        ckpt_id = str((hist_config.get("configurable") or {}).get("checkpoint_id") or "")
        if not ckpt_id:
            continue
        hist_values = hist.values if hist.values else values
        return dict(hist_config), hist_next, hist_values
    return None


async def _find_crash_bookmark_from_history(
    graph: Any,
    config: Mapping[str, Any],
) -> tuple[Any, dict[str, Any], tuple[str, ...]] | None:
    """Найти durable checkpoint обрыва через ``graph.aget_state_history`` (newest-first).

    Обходит историю thread до первого snapshot, у которого вложенный подграф ещё
    имеет непустой ``next`` на hop обрыва (``diagnostic``, ``dispatch_cycle``, …).
    Снимок, у которого pending-задача есть, а ``aget_state`` подграфа уже пуст,
    пропускается: ``ainvoke(None)`` на нём ничего не запускает.

    Returns:
        ``(target_graph, invoke_config, own_next)`` — граф-владелец snapshot с непустым
        ``aget_state(invoke_config).next`` на hop обрыва (``static_repair`` или verify parent).
    """
    config_dict = _with_checkpointer_config(graph, config)
    history = graph.aget_state_history(config_dict)
    thread_id = _thread_id_from_config(config_dict)
    scanned = 0
    async for hist in history:
        scanned += 1
        hist_config = hist.config
        if not hist_config:
            logger.debug(
                "checkpoint history_bookmark skip[%s] thread_id=%s reason=missing hist.config",
                scanned,
                thread_id,
            )
            continue
        hist_ckpt = str((hist_config.get("configurable") or {}).get("checkpoint_id") or "-")
        pending = await _resolve_nested_pending_subgraph(graph, hist_config)
        if pending is None:
            logger.debug(
                "checkpoint history_bookmark skip[%s] thread_id=%s checkpoint_id=%s reason=no nested pending",
                scanned,
                thread_id,
                hist_ckpt,
            )
            continue
        subgraph, sub_config, next_nodes = pending
        pinned = await _subgraph_checkpoint_still_pending(
            subgraph,
            sub_config,
            next_nodes,
        )
        if pinned is None:
            logger.debug(
                "checkpoint history_bookmark skip[%s] thread_id=%s checkpoint_id=%s "
                "reason=subgraph live next empty pending_next=%s ns=%s",
                scanned,
                thread_id,
                hist_ckpt,
                next_nodes,
                _checkpoint_ns_from_config(sub_config) or "-",
            )
            continue
        _resume_config, resume_next, values = pinned
        if not _nested_pending_worthy(values or {}, resume_next):
            score = _pending_checkpoint_score(values or {}, resume_next)
            logger.debug(
                "checkpoint history_bookmark skip[%s] thread_id=%s checkpoint_id=%s "
                "reason=not_worthy score=%s resume_next=%s verdict=%s",
                scanned,
                thread_id,
                hist_ckpt,
                score,
                resume_next,
                str((values or {}).get("verdict") or "-"),
            )
            continue
        target = await _resolve_history_bookmark_invoke_target(
            graph,
            hist_config,
            subgraph,
            pinned,
        )
        if target is None:
            logger.debug(
                "checkpoint history_bookmark skip[%s] thread_id=%s checkpoint_id=%s "
                "reason=no invoke target resume_next=%s",
                scanned,
                thread_id,
                hist_ckpt,
                resume_next,
            )
            continue
        target_graph, invoke_config, resume_next = target
        configurable = invoke_config.get("configurable") or {}
        logger.debug(
            "checkpoint decision=history_bookmark_resume checkpoint_id=%s checkpoint_ns=%s next=%s scanned=%s",
            str(configurable.get("checkpoint_id") or "-"),
            _checkpoint_ns_from_config(invoke_config) or "-",
            resume_next,
            scanned,
        )
        return target_graph, invoke_config, resume_next
    logger.debug(
        "checkpoint path=aget_state_history crash_bookmark=not_found thread_id=%s "
        "scanned=%s reason=no history entry with actionable nested hop",
        thread_id,
        scanned,
    )
    return None


async def _nested_subgraph_has_pending_checkpoint(
    graph: Any,
    config: Mapping[str, Any],
) -> bool:
    """True, если nested subgraph wrapper имеет незавершённый ``snapshot.next``."""
    return (await _resolve_nested_pending_subgraph(graph, config)) is not None


def _terminal_verify_pause(state: Mapping[str, Any]) -> bool:
    """An intentional PAUSE is terminal for this verify invocation.

    The plan-artifact gate fails closed on stale/missing frozen inputs. It
    must never be re-entered by the stale-END recovery algorithm: only the
    authorized plan producer may rebuild the missing/stale evidence.

    Coverage Activation retains its separate legacy migration path.
    """
    stop = str(state.get("verify_stop_reason") or "").strip()
    scenario = str(state.get("scenario_coverage_verdict") or "").strip()
    return stop in {"Owner Attention", "PLAN_ARTIFACTS_PAUSED"} or (
        bool(stop) and scenario == "SCENARIO_TEST_COVERAGE_UNKNOWN"
    )


def _lane_terminal_superseded_by_super_implement_plan(state: Mapping[str, Any]) -> bool:
    """Durable lane PAUSED/FAILED не terminal, если SoT plan всё ещё ведёт вперёд (RUNNING).

    После ``-r`` или recovery в checkpoint может остаться runtime PAUSE (stagnation,
    STAGE_NOT_WIRED, SDK hop), тогда как ``opsx-super-implement.mjs plan`` уже вернул
    ``APPLY_IMPLEMENT`` / иной forward stage. Stale END должен опираться на plan JSON
    в state, а не на устаревший ``implementation_status``.
    """
    from workflows.implementation.routing_contract import (
        RUN_STATUS_FAILED,
        RUN_STATUS_PAUSED,
        RUN_STATUS_RUNNING,
        STAGE_RECOVERY_PAUSED,
        parse_super_implement_plan_payload,
    )

    status = str(state.get("implementation_status") or "").strip()
    if status not in {RUN_STATUS_PAUSED, RUN_STATUS_FAILED}:
        return False
    raw = state.get("implementation_plan")
    if not isinstance(raw, dict):
        return False
    step = parse_super_implement_plan_payload(raw)
    if step.status != RUN_STATUS_RUNNING:
        return False
    next_stage = str(step.next_stage or "").strip()
    if not next_stage or next_stage == STAGE_RECOVERY_PAUSED:
        return False
    return True


def _routing_state_for_stale_implementation_resume(
    state: Mapping[str, Any],
) -> Mapping[str, Any]:
    """State для conditional edges при stale resume, если plan supersede lane terminal."""
    if not _lane_terminal_superseded_by_super_implement_plan(state):
        return state
    from workflows.implementation.routing_contract import plan_transition_patch_for_step

    raw = state.get("implementation_plan")
    if not isinstance(raw, dict):
        return state
    from workflows.implementation.routing_contract import parse_super_implement_plan_payload

    step = parse_super_implement_plan_payload(raw)
    return {**dict(state), **plan_transition_patch_for_step(step)}


def stale_implementation_plan_resume_state_patch(state: Mapping[str, Any]) -> dict[str, Any]:
    """Сброс runtime PAUSE/FAILED перед ``Command(goto=…)`` по свежему plan JSON."""
    if not _lane_terminal_superseded_by_super_implement_plan(state):
        return {}
    from workflows.implementation.routing_contract import (
        parse_super_implement_plan_payload,
        plan_transition_patch_for_step,
    )

    raw = state.get("implementation_plan")
    if not isinstance(raw, dict):
        return {}
    step = parse_super_implement_plan_payload(raw)
    return plan_transition_patch_for_step(step)


def _terminal_implementation_lane_pause(state: Mapping[str, Any]) -> bool:
    """Implementation lane завершён (PAUSED/FAILED/APPLY_COMPLETE) — не «залечивать» через stale END.

    Иначе ``plan_stale_end`` снова шлёт ``Command(goto=apply_design_lane)`` и повторяет
    тот же apply-hop после честного stagnation/BLOCKED (в т.ч. на свежем ``-r`` прогоне).
    Успешный ``APPLY_COMPLETE`` на implementation-only parent тоже terminal: verify stale
    resume не должен блокировать CLI hop.

    Исключение: plan SoT ``RUNNING`` + forward ``next_stage`` перекрывает runtime PAUSE/FAILED
    in checkpoint (см. ``_lane_terminal_superseded_by_super_implement_plan``).
    """
    from workflows.implementation.routing_contract import (
        RUN_STATUS_APPLY_COMPLETE,
        RUN_STATUS_FAILED,
        RUN_STATUS_PAUSED,
    )

    if _lane_terminal_superseded_by_super_implement_plan(state):
        return False
    status = str(state.get("implementation_status") or "").strip()
    return status in {RUN_STATUS_PAUSED, RUN_STATUS_FAILED, RUN_STATUS_APPLY_COMPLETE}


async def plan_stale_end_resume(
    graph: Any,
    *,
    config: Mapping[str, Any],
    snapshot: Any,
) -> StaleEndResumePlan | None:
    """Спланировать resume после END, если текущий граф добавил successor.

    Учитывает nested subgraph (``verify`` в ``build_main_graph``): ищет
    устаревший END в checkpoint history subgraph и продолжает через
    ``Command(goto=...)`` без повторного finished-node.
    """
    if not durable_langgraph_checkpoint_enabled():
        logger.debug(
            "checkpoint plan_stale_end skip reason=ephemeral_no_stale_resume durable=%s",
            durable_langgraph_checkpoint_enabled(),
        )
        return None
    if snapshot.next or not snapshot.values:
        logger.debug(
            "checkpoint plan_stale_end skip reason=snapshot_not_at_end next=%s has_values=%s",
            tuple(snapshot.next or ()),
            bool(snapshot.values),
        )
        return None
    if _terminal_verify_pause(snapshot.values):
        logger.info("checkpoint plan_stale_end refused: terminal verify PAUSE")
        return None
    # A terminal PAUSE from an active mutating SDK run is not a stale END to
    # heal automatically. Its prior effects have unknown completion identity.
    if str(snapshot.values.get("verify_apply_block_reason") or "").strip():
        logger.warning(
            "checkpoint plan_stale_end refused: unresolved active Cursor run "
            "(no automatic re-dispatch)"
        )
        return None
    if _terminal_implementation_lane_pause(snapshot.values):
        logger.info(
            "checkpoint plan_stale_end refused: implementation lane terminal PAUSE/FAILED"
        )
        return None
    config_dict = _with_checkpointer_config(graph, config)
    thread_id = _thread_id_from_config(config_dict)
    logger.debug(
        "checkpoint plan_stale_end begin thread_id=%s %s",
        thread_id,
        format_checkpoint_snapshot_summary(snapshot, config_dict),
    )
    routing_state = _routing_state_for_stale_implementation_resume(snapshot.values)
    successor = await _resolve_stale_end_on_snapshot(
        graph,
        config_dict,
        snapshot,
        routing_state,
    )
    if successor is not None and parent_command_goto_is_valid(graph, successor):
        logger.debug(
            "checkpoint plan_stale_end chose=root_command_goto thread_id=%s successor=%s",
            thread_id,
            successor,
        )
        return StaleEndResumePlan(graph, config_dict, successor)

    finished_node = await _infer_checkpoint_finished_node(graph, config_dict)
    logger.debug(
        "checkpoint plan_stale_end root_successor=%s finished_node=%s thread_id=%s",
        successor,
        finished_node,
        thread_id,
    )
    plan = await _plan_stale_end_in_subgraph_wrappers(
        graph,
        config_dict,
        finished_node=finished_node,
    )
    if plan is None:
        logger.debug(
            "checkpoint plan_stale_end=none thread_id=%s finished_node=%s "
            "reason=no stale subgraph successor (durable head считается завершённым)",
            thread_id,
            finished_node,
        )
    else:
        logger.debug(
            "checkpoint plan_stale_end chose=subgraph_command_goto thread_id=%s successor=%s ns=%s",
            thread_id,
            plan.successor,
            _checkpoint_ns_from_config(plan.config) or "-",
        )
    return plan


async def resolve_stale_end_resume_successor(
    graph: Any,
    *,
    config: Mapping[str, Any],
    snapshot: Any,
) -> str | None:
    """Выбрать successor, если snapshot на END, а текущий граф ведёт дальше.

    Возвращает имя узла для ``Command(goto=...)`` или ``None``, если run
    действительно завершён (successor — ``END`` или отсутствует).
    """
    plan = await plan_stale_end_resume(graph, config=config, snapshot=snapshot)
    if plan is None:
        return None
    return plan.successor


async def static_repair_rewind_report(
    graph: Any,
    *,
    config: Mapping[str, Any],
) -> str | None:
    """Текст для Owner, если rewind на ``static_repair`` нужен, но history пуст."""
    if "verify" not in graph.nodes:
        return None
    config_dict = _with_checkpointer_config(graph, config)
    try:
        verify_subgraph, sub_config = verify_subgraph_invoke_config(graph, config_dict)
    except ValueError:
        return None
    current = await verify_subgraph.aget_state(sub_config)
    if current is None or not current.values:
        return None
    current_next = tuple(current.next or ())
    if "static_repair" in current_next:
        return None
    if not _verify_bad_skip_needs_rewind(current.values, current_next):
        return None
    if await plan_static_repair_rewind(graph, config=config) is not None:
        return None
    return "static_repair rewind: no checkpoint in thread history"


def _verify_bad_skip_needs_rewind(
    values: Mapping[str, Any],
    next_nodes: Sequence[str],
) -> bool:
    """True, если durable head ушёл мимо gate_repair (``project_bug`` или ложный SKIP)."""
    if "project_bug" in next_nodes:
        return True
    if next_nodes:
        return False
    skipped = str(values.get("static_repair_status") or "") == "SKIPPED_PASS"
    missing_gates = not str(values.get("quality_gates_execution_status") or "").strip()
    return skipped and missing_gates


async def plan_static_repair_rewind(
    graph: Any,
    *,
    config: Mapping[str, Any],
) -> StaticRepairRewindPlan | None:
    """Выбрать checkpoint verify-subgraph для time-travel на узел ``static_repair``.

    Обходит ``verify_subgraph.aget_state_history`` (newest-first) и берёт первый snapshot,
    у которого ``next`` содержит ``static_repair``, если текущий head уже на ``project_bug``
    или зафиксировал ``STATIC_REPAIR_SKIP`` без выполненных gates.
    """
    if "verify" not in graph.nodes:
        return None
    config_dict = _with_checkpointer_config(graph, config)
    try:
        verify_subgraph, sub_config = verify_subgraph_invoke_config(graph, config_dict)
    except ValueError:
        return None

    current = await verify_subgraph.aget_state(sub_config)
    if current is None or not current.values:
        return None
    current_next = tuple(current.next or ())
    if "static_repair" in current_next:
        return None
    if not _verify_bad_skip_needs_rewind(current.values, current_next):
        return None

    history = verify_subgraph.aget_state_history(sub_config)
    async for hist in history:
        hist_next = tuple(hist.next or ())
        if "static_repair" not in hist_next:
            continue
        hist_configurable = dict((hist.config or {}).get("configurable") or {})
        ckpt_id = str(hist_configurable.get("checkpoint_id") or "")
        if not ckpt_id:
            continue
        return StaticRepairRewindPlan(
            verify_subgraph,
            dict(hist.config),
            ckpt_id,
        )
    return None


def _attach_verify_rewind_note(result: GraphState, note: str | None) -> GraphState:
    if not note:
        return result
    merged = dict(result)
    merged["verify_rewind_note"] = note
    return merged


async def _resume_pending_subgraph(
    graph: Any,
    *,
    config: Mapping[str, Any],
    repo_root: Path,
    rewind_note: str | None,
    fallback_values: Mapping[str, Any],
    pending: tuple[Any, dict[str, Any], tuple[str, ...]] | tuple[Any, dict[str, Any]] | None = None,
) -> GraphState:
    """``target_graph.ainvoke(None)`` для выбранного pending nested target или history bookmark."""
    expected_next: tuple[str, ...] = ()
    if pending is None:
        resolved = await _resolve_nested_pending_subgraph(graph, config)
        if resolved is None:
            logger.debug("checkpoint decision=resume_pending_subgraph skipped (no pending)")
            return _attach_verify_rewind_note(dict(fallback_values), rewind_note)
        target_graph, invoke_config, expected_next = resolved
    else:
        if len(pending) == 3:
            target_graph, invoke_config, expected_next = pending
        else:
            target_graph, invoke_config = pending  # type: ignore[misc]
    invoke_config = _with_checkpointer_config(target_graph, invoke_config)
    use_subgraphs = target_graph is graph
    if expected_next:
        actionable = await _invoke_config_has_actionable_pending(
            target_graph,
            invoke_config,
            expected_next,
        )
        if not actionable:
            live_probe = await _effective_pending_next_at_config(
                target_graph,
                invoke_config,
                subgraphs=use_subgraphs,
            )
            if not live_probe:
                logger.debug(
                    "checkpoint decision=resume_pending_subgraph skipped empty_next checkpoint_ns=%s",
                    _checkpoint_ns_from_config(invoke_config) or "-",
                )
                return _attach_verify_rewind_note(dict(fallback_values), rewind_note)
    else:
        live_probe = await _effective_pending_next_at_config(
            target_graph,
            invoke_config,
            subgraphs=use_subgraphs,
        )
        if not live_probe:
            logger.debug(
                "checkpoint decision=resume_pending_subgraph skipped empty_next checkpoint_ns=%s",
                _checkpoint_ns_from_config(invoke_config) or "-",
            )
            return _attach_verify_rewind_note(dict(fallback_values), rewind_note)
        expected_next = live_probe

    if await _ainvoke_would_restart_impl_verify(
        graph,
        target_graph,
        invoke_config,
        expected_next,
    ):
        verify_parent = await _find_verify_parent_static_repair_resume(
            graph,
            config,
            expected_next,
        )
        if verify_parent is not None:
            target_graph, invoke_config, expected_next = verify_parent
            invoke_config = _with_checkpointer_config(target_graph, invoke_config)
            use_subgraphs = target_graph is graph
        else:
            logger.debug("checkpoint resume refused: would restart impl_verify")
            return _attach_verify_rewind_note(dict(fallback_values), rewind_note)

    target_snapshot = await target_graph.aget_state(invoke_config)
    sub_update = getattr(target_graph, "aupdate_state", None)
    if sub_update is not None and target_snapshot and target_snapshot.values:
        sub_patch = graph_repo_path_patch(target_snapshot.values, repo_root)
        _ = await sub_update(invoke_config, sub_patch)
    first_target = target_graph
    progressed = await _checkpoint_ainvoke_resume(
        target_graph,
        invoke_config,
        use_subgraphs=use_subgraphs,
    )
    if not progressed and _is_static_repair_interior_hop(expected_next):
        nested_plan = await find_wrapper_hop_nested_resume(
            graph,
            target_graph,
            invoke_config,
            expected_next,
        )
        if nested_plan is not None:
            nested_graph, nested_config, nested_next = nested_plan
            nested_config = _with_checkpointer_config(nested_graph, nested_config)
            logger.debug(
                "checkpoint decision=wrapper_nested_resume checkpoint_ns=%s next=%s",
                _checkpoint_ns_from_config(nested_config) or "-",
                nested_next,
            )
            progressed = await _checkpoint_ainvoke_resume(
                nested_graph,
                nested_config,
                use_subgraphs=nested_graph is graph,
            )
            if progressed:
                target_graph = nested_graph
                invoke_config = nested_config
        if not progressed and first_target is not graph:
            verify_parent = await _find_verify_parent_static_repair_resume(
                graph,
                config,
                expected_next,
            )
            if verify_parent is not None:
                target_graph, invoke_config, _expected = verify_parent
                invoke_config = _with_checkpointer_config(target_graph, invoke_config)
                progressed = await _checkpoint_ainvoke_resume(
                    target_graph,
                    invoke_config,
                    use_subgraphs=target_graph is graph,
                )
        if not progressed:
            stuck_hop = str(expected_next[0])
            rewind = await _find_runnable_predecessor_rewind_plan(
                first_target,
                invoke_config,
                stuck_hop,
            )
            if rewind is not None:
                rewind_config, _rewind_next, predecessor = rewind
                rewind_config = _with_checkpointer_config(first_target, rewind_config)
                rewind_ckpt = str(
                    (rewind_config.get("configurable") or {}).get("checkpoint_id") or "-"
                )
                logger.debug(
                    "checkpoint decision=interior_rewind target=%s checkpoint_id=%s stuck=%s",
                    predecessor,
                    rewind_ckpt,
                    stuck_hop,
                )
                progressed = await _checkpoint_ainvoke_resume(
                    first_target,
                    rewind_config,
                    use_subgraphs=first_target is graph,
                )
    if not progressed:
        stalled = await _effective_pending_next_at_config(
            graph,
            config,
            subgraphs=True,
        )
        if _next_matches_pending(stalled, expected_next) or _is_static_repair_interior_hop(
            stalled,
        ):
            logger.debug("checkpoint resume stalled pending_next=%s live=%s", expected_next, stalled)
        return _attach_verify_rewind_note(dict(fallback_values), rewind_note)
    refreshed = await graph.aget_state(config)
    if refreshed and refreshed.values:
        return _attach_verify_rewind_note(dict(refreshed.values), rewind_note)
    sub_snapshot = await target_graph.aget_state(invoke_config)
    if sub_snapshot and sub_snapshot.values:
        return _attach_verify_rewind_note(dict(sub_snapshot.values), rewind_note)
    return _attach_verify_rewind_note(dict(fallback_values), rewind_note)


async def prepare_graph_invocation(
    graph: Any,
    *,
    thread_id: str,
    initial_state: GraphState,
) -> GraphState | Command | None:
    """Вернуть input для ``graph.ainvoke`` / ``astream`` на **этом** ``graph``.

    ``None`` — resume pending checkpoint, pending nested task (``subgraph.ainvoke(None)``)
    или stale END в nested subgraph (нужен ``invoke_checkpointed_graph``); ``Command(goto=...)`` —
    только если successor принадлежит этому графу; ``GraphState`` — новый run или уже завершённый thread.
    """
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = await graph.aget_state(config)
    if snapshot and snapshot.values and not snapshot.next:
        if await _nested_subgraph_has_pending_checkpoint(graph, config):
            return None
        plan = await plan_stale_end_resume(graph, config=config, snapshot=snapshot)
        if plan is None:
            return dict(snapshot.values)
        if plan.target_graph is graph and parent_command_goto_is_valid(graph, plan.successor):
            return Command(goto=plan.successor)
        return None
    if snapshot and snapshot.values and snapshot.next:
        return None
    return initial_state


_MAX_VERIFY_PIPELINE_CONTINUATIONS = 6


async def _apply_stale_end_resume_plan(
    graph: Any,
    plan: StaleEndResumePlan,
    *,
    config: Mapping[str, Any],
    snapshot: Any,
    rewind_note: str | None,
) -> GraphState:
    """``Command(goto=…)`` на ``plan.target_graph`` после stale END (coverage gap и др.)."""
    successor = normalize_stale_apply_successor(plan.target_graph, plan.successor)
    if plan.target_graph is not graph:
        await _scrub_stale_root_pending_goto(graph, config, snapshot)
    resume_update = {
        **stale_coverage_activation_resume_state_patch(snapshot.values),
        **stale_implementation_plan_resume_state_patch(snapshot.values),
    }
    goto_cmd: Command = (
        Command(goto=successor, update=resume_update)
        if resume_update
        else Command(goto=successor)
    )
    result = await plan.target_graph.ainvoke(
        goto_cmd,
        config=plan.config,
        durability="sync",
    )
    return _attach_verify_rewind_note(result, rewind_note)  # type: ignore[arg-type]


async def _maybe_continue_incomplete_verify_pipeline(
    graph: Any,
    *,
    config: Mapping[str, Any],
    thread_id: str,
    result: GraphState,
    rewind_note: str | None,
) -> GraphState:
    """После ``ainvoke``: если pipeline оборвался на coverage без gates — stale resume в том же CLI hop."""
    from workflows.verify.cli_outcome import verify_impl_pipeline_complete

    if not graph_invoke_continuation_enabled(graph):
        return result

    merged = result
    for _ in range(_MAX_VERIFY_PIPELINE_CONTINUATIONS):
        if _terminal_verify_pause(merged):
            return merged
        if verify_impl_pipeline_complete(merged):
            return merged
        snapshot = await graph.aget_state(config)
        if snapshot is None or not snapshot.values or snapshot.next:
            logger.info(
                "checkpoint ephemeral continue=skip thread_id=%s missing_values=%s next=%s",
                thread_id,
                snapshot is None or not (snapshot and snapshot.values),
                tuple(snapshot.next or ()) if snapshot else (),
            )
            return merged
        plan = await plan_stale_end_resume(graph, config=config, snapshot=snapshot)
        if plan is None:
            logger.info(
                "checkpoint ephemeral continue=skip thread_id=%s reason=plan_stale_end_none",
                thread_id,
            )
            return merged
        coerced = await _coerce_implementation_stale_successor_to_execute(
            plan.target_graph,
            plan.config,
            plan.successor,
            snapshot.values,
        )
        if coerced != plan.successor:
            plan = StaleEndResumePlan(plan.target_graph, plan.config, coerced)
        logger.debug(
            "checkpoint decision=post_invoke_stale_end_resume successor=%s thread_id=%s",
            plan.successor,
            thread_id,
        )
        merged = await _apply_stale_end_resume_plan(
            graph,
            plan,
            config=config,
            snapshot=snapshot,
            rewind_note=rewind_note,
        )
    return merged


async def invoke_checkpointed_graph(
    graph: Any,
    *,
    initial_state: GraphState,
    thread_id: str,
    repo_root: Path | None = None,
) -> GraphState:
    """``ainvoke`` с durable checkpoint без повторного прохода завершённых узлов.

    Pending task во nested verify-subgraph (``next=('apply',)`` при пустом root
    ``next``) продолжается через ``subgraph.ainvoke(None)``, не через корневой
    ``ainvoke`` — иначе возвращается сохранённый terminal state без apply.

    Stale END в verify-subgraph продолжается через ``plan.target_graph.ainvoke``,
    а не ``Command(goto=...)`` на корневом ``build_main_graph``.

    При head verify-subgraph на ``project_bug`` после ложного SKIP gate_repair ищет
    checkpoint с ``next=static_repair`` в ``aget_state_history`` и делает time-travel
    ``ainvoke(None, config=<snapshot.config>)`` (stderr ``rewind: static_repair …``).

    Если durable head не показывает nested pending, но в thread history есть checkpoint
    ``dispatch_cycle`` / ``diagnostic`` в ``static_repair``, resume идёт на **владельца**
    snapshot с ``checkpoint_ns=verify:…|static_repair:…`` (или verify parent с
    ``next=static_repair``), не на корневой checkpoint — иначе снова ``verify_cycle_gate``.
    """
    if not durable_langgraph_checkpoint_enabled():
        logger.info(
            "checkpoint invoke redirect=ephemeral thread_id=%s durable=%s",
            thread_id,
            durable_langgraph_checkpoint_enabled(),
        )
        return await invoke_ephemeral_workflow_graph(
            graph,
            initial_state=initial_state,
            repo_root=repo_root,
            thread_id=thread_id,
        )

    if repo_root is None:
        raw = initial_state.get("repository_path") or initial_state.get("workspace")
        if not raw:
            raise ValueError("repository_path required when repo_root is omitted")
        repo_root = Path(str(raw))
    repo_root = resolve_opsx_repository_root(repo_root)
    from workflows.repair.repair_adapter_port import default_hop_model_selection

    model_selection = initial_state.get("model") or default_hop_model_selection()
    path_patch = graph_repo_path_patch(initial_state, repo_root)
    initial_state = {**initial_state, **path_patch, "model": model_selection}
    config = {"configurable": {"thread_id": thread_id}}
    snapshot = await graph.aget_state(config)
    if snapshot and snapshot.values:
        prior_provider = str(snapshot.values.get("execution_provider") or "cursor")
        target_provider = str(initial_state.get("execution_provider") or "cursor")
        if prior_provider != target_provider:
            raise RuntimeError(
                "PROVIDER_IDENTITY_MISMATCH: prior=" + prior_provider
                + " selected=" + target_provider
            )
        if target_provider == "codex":
            prior_model = snapshot.values.get("model")
            prior_id = prior_model.get("id") if isinstance(prior_model, Mapping) else None
            if prior_id != model_selection.get("id"):
                raise RuntimeError("MODEL_IDENTITY_MISMATCH")
    update = getattr(graph, "aupdate_state", None)
    resume_patch = {**path_patch, "model": model_selection}
    if initial_state.get("execution_provider"):
        resume_patch["execution_provider"] = initial_state["execution_provider"]
    if update is not None and snapshot and snapshot.values:
        prior = snapshot.values.get("model")
        if prior != model_selection:
            prior_id = prior.get("id") if isinstance(prior, Mapping) else None
            logger.info(
                "checkpoint refresh model thread_id=%s %s -> %s",
                thread_id,
                prior_id or prior or "-",
                model_selection.get("id"),
            )
        update_result = update(config, resume_patch)
        if inspect.isawaitable(update_result):
            _ = await update_result
        snapshot = await graph.aget_state(config)

    # Do not reinterpret an intentional Owner Attention terminal checkpoint as
    # a crash bookmark or a missing successor on the next CLI invocation.
    if snapshot and snapshot.values and not snapshot.next and _terminal_verify_pause(snapshot.values):
        logger.info("checkpoint exit=terminal_verify_pause thread_id=%s", thread_id)
        return dict(snapshot.values)

    has_snapshot = bool(snapshot and snapshot.values)
    snapshot_next = tuple(snapshot.next or ()) if snapshot else ()
    logger.debug(
        "checkpoint invoke head thread_id=%s %s %s",
        thread_id,
        format_checkpoint_snapshot_summary(snapshot, config),
        _format_tasks_summary(tuple(snapshot.tasks or ()) if snapshot else ()),
    )
    invoke_msg = (
        "checkpoint invoke thread_id=%s has_snapshot=%s snapshot.next=%s",
        thread_id,
        has_snapshot,
        snapshot_next,
    )
    if has_snapshot and not snapshot_next:
        logger.debug(*invoke_msg)
    else:
        logger.debug(*invoke_msg)

    rewind_plan = await plan_static_repair_rewind(graph, config=config)
    if rewind_plan is None:
        logger.debug("checkpoint path=static_repair_rewind result=none thread_id=%s", thread_id)
    else:
        logger.debug(
            "checkpoint path=static_repair_rewind result=plan thread_id=%s checkpoint_id=%s",
            thread_id,
            rewind_plan.checkpoint_id,
        )
    rewind_note = await static_repair_rewind_report(graph, config=config)
    if rewind_note:
        logger.debug("checkpoint note=%s thread_id=%s", rewind_note, thread_id)
    nested_pending: tuple[Any, dict[str, Any], tuple[str, ...]] | None = None
    nested_worthy = False
    history_pending: tuple[Any, dict[str, Any], tuple[str, ...]] | None = None
    if rewind_plan is None and snapshot and snapshot.values:
        nested_pending = await _resolve_nested_pending_subgraph(graph, config)
        if nested_pending is not None:
            _subgraph, sub_config, pending_next = nested_pending
            sub_snapshot = await _subgraph.aget_state(sub_config)
            nested_worthy = _nested_pending_worthy(
                (sub_snapshot.values if sub_snapshot else {}) or {},
                pending_next,
            )
            if nested_worthy:
                logger.debug(
                    "checkpoint path=aget_state_subgraphs task checkpoint_ns=%s next=%s worthy=True",
                    _checkpoint_ns_from_config(sub_config) or "-",
                    pending_next or "-",
                )
            else:
                score = _pending_checkpoint_score(
                    (sub_snapshot.values if sub_snapshot else {}) or {},
                    pending_next,
                )
                logger.debug(
                    "checkpoint path=aget_state_subgraphs pending=not_worthy thread_id=%s "
                    "score=%s next=%s %s",
                    thread_id,
                    score,
                    pending_next,
                    format_checkpoint_snapshot_summary(sub_snapshot, sub_config),
                )
        else:
            logger.debug(
                "checkpoint path=aget_state_subgraphs pending=none thread_id=%s "
                "reason=_resolve_nested_pending_subgraph returned None",
                thread_id,
            )

        if not nested_worthy:
            logger.debug(
                "checkpoint path=aget_state_history search=crash_bookmark thread_id=%s",
                thread_id,
            )
            history_pending = await _find_crash_bookmark_from_history(graph, config)
            if history_pending is not None:
                _hg, _hc, hist_next = history_pending
                logger.debug(
                    "checkpoint path=aget_state_history crash_bookmark=found thread_id=%s next=%s ns=%s",
                    thread_id,
                    hist_next,
                    _checkpoint_ns_from_config(_hc) or "-",
                )

    effective_pending = nested_pending if nested_worthy else history_pending
    logger.debug(
        "checkpoint decision=effective_pending thread_id=%s source=%s present=%s",
        thread_id,
        "nested_subgraphs" if nested_worthy else ("history_bookmark" if history_pending else "none"),
        effective_pending is not None,
    )

    if rewind_plan is not None:
        logger.debug(
            "checkpoint decision=static_repair_rewind checkpoint_id=%s thread_id=%s",
            rewind_plan.checkpoint_id,
            thread_id,
        )
        logger.debug(
            "rewind: static_repair checkpoint=%s thread_id=%s",
            rewind_plan.checkpoint_id,
            thread_id,
        )
        rewind_result = await rewind_plan.target_graph.ainvoke(
            None,
            config=rewind_plan.config,
            durability="sync",
        )
        refreshed = await graph.aget_state(config)
        if refreshed and refreshed.next:
            continued = await graph.ainvoke(
                None,
                config=config,
                durability="sync",
            )
            return _attach_verify_rewind_note(continued, rewind_note)  # type: ignore[arg-type]
        if isinstance(rewind_result, dict):
            return _attach_verify_rewind_note(dict(rewind_result), rewind_note)
        refreshed_values = refreshed.values if refreshed and refreshed.values else {}
        return _attach_verify_rewind_note(dict(refreshed_values), rewind_note)

    if snapshot and snapshot.values and not snapshot.next:
        if effective_pending is not None:
            logger.debug(
                "checkpoint decision=branch resume_pending_subgraph thread_id=%s "
                "reason=root next empty with effective pending",
                thread_id,
            )
            return await _resume_pending_subgraph(
                graph,
                config=config,
                repo_root=repo_root,
                rewind_note=rewind_note,
                fallback_values=snapshot.values,
                pending=effective_pending,
            )
        plan = await plan_stale_end_resume(graph, config=config, snapshot=snapshot)
        if plan is None:
            logger.debug(
                "checkpoint exit=return_terminal_snapshot function=invoke_checkpointed_graph "
                "thread_id=%s reason=no effective_pending and plan_stale_end_resume is None "
                "%s (CLI вернёт сохранённый state без нового hop)",
                thread_id,
                format_checkpoint_snapshot_summary(snapshot, config),
            )
            terminal = _attach_verify_rewind_note(dict(snapshot.values), rewind_note)
            return terminal
        logger.debug(
            "checkpoint decision=stale_end_resume successor=%s ns=%s thread_id=%s target=%s",
            plan.successor,
            _checkpoint_ns_from_config(plan.config) or "-",
            thread_id,
            _graph_invoke_label(plan.target_graph),
        )
        merged = await _apply_stale_end_resume_plan(
            graph,
            plan,
            config=config,
            snapshot=snapshot,
            rewind_note=rewind_note,
        )
        merged = await _maybe_continue_incomplete_verify_pipeline(
            graph,
            config=config,
            thread_id=thread_id,
            result=merged,
            rewind_note=rewind_note,
        )
        return merged
    if snapshot and snapshot.values and snapshot.next:
        if effective_pending is not None:
            return await _resume_pending_subgraph(
                graph,
                config=config,
                repo_root=repo_root,
                rewind_note=rewind_note,
                fallback_values=snapshot.values,
                pending=effective_pending,
            )
        logger.debug(
            "checkpoint decision=root_ainvoke_resume snapshot.next=%s thread_id=%s",
            tuple(snapshot.next),
            thread_id,
        )
        invocation: GraphState | Command | None = None
    else:
        logger.debug(
            "checkpoint decision=fresh_start thread_id=%s has_snapshot=%s",
            thread_id,
            has_snapshot,
        )
        invocation = initial_state
    logger.debug(
        "checkpoint decision=root_ainvoke_begin thread_id=%s invocation_type=%s",
        thread_id,
        type(invocation).__name__,
    )
    result = await graph.ainvoke(
        invocation,
        config=config,
        durability="sync",
    )
    merged = _attach_verify_rewind_note(result, rewind_note)  # type: ignore[arg-type]
    merged = await _maybe_continue_incomplete_verify_pipeline(
        graph,
        config=config,
        thread_id=thread_id,
        result=merged,
        rewind_note=rewind_note,
    )
    return merged


async def invoke_ephemeral_workflow_graph(
    graph: Any,
    *,
    initial_state: GraphState,
    repo_root: Path | None = None,
    thread_id: str = "",
) -> GraphState:
    """Выполнить compiled workflow graph без durable checkpoint (один ``ainvoke``).

    Используется verify-impl / parent PP, когда ``durable_langgraph_checkpoint_enabled()``
    ложно: без SQLite и без post-``ainvoke`` ``_maybe_continue_incomplete_verify_pipeline``.
    MemorySaver допустим для conditional edges внутри того же hop, но внешний CLI loop
    не должен повторять stale-end resume.
    """
    if repo_root is None:
        raw = initial_state.get("repository_path") or initial_state.get("workspace")
        if not raw:
            raise ValueError("repository_path required when repo_root is omitted")
        repo_root = Path(str(raw))
    repo_root = resolve_opsx_repository_root(repo_root)
    from workflows.repair.repair_adapter_port import default_hop_model_selection

    model_selection = initial_state.get("model") or default_hop_model_selection()
    path_patch = graph_repo_path_patch(initial_state, repo_root)
    merged_initial: GraphState = {
        **initial_state,
        **path_patch,
        "model": model_selection,
    }
    effective_thread = thread_id or str(merged_initial.get("run_uuid") or "ephemeral")
    config = {"configurable": {"thread_id": effective_thread}}
    logger.info(
        "checkpoint mode=ephemeral thread_id=%s durable=%s",
        effective_thread,
        durable_langgraph_checkpoint_enabled(),
    )
    # Без checkpointer durability="sync" в LangGraph всё равно ждёт _put_checkpoint_fut
    # (UserWarning + AttributeError в installed pregel); durable sync — только с saver.
    result = await graph.ainvoke(merged_initial, config=config)
    if isinstance(result, dict):
        return dict(result)
    return dict(merged_initial)
