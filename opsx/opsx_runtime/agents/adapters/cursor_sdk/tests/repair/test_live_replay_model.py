"""Opt-in real adapter replay of recorded ScenarioCoverage FAIL.

Replay is NOT an OPSX Verify execution and produces NO production gate evidence.
The model may edit only a disposable checkout pinned to a full Git commit SHA.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from contextlib import nullcontext, suppress
from datetime import datetime, timezone
from pathlib import Path

import pytest

CHANGE_ID = "external-client-notifications-pilot"
RECORDED_RUN = "56736d90f28c"


def _progress(request: pytest.FixtureRequest, stage: str, **details: object) -> None:
    """Emit one live terminal line even under pytest's default fd capture.

    No prompts, SDK responses, secrets or personal data are printed.
    """
    time_utc = datetime.now(timezone.utc).strftime("%H:%M:%S")
    fields = " ".join(f"{key}={value}" for key, value in details.items())
    line = f"[OPSX-LIVE {time_utc}Z] {stage}"
    if fields:
        line += " " + fields
    capture = request.config.pluginmanager.getplugin("capturemanager")
    context = capture.global_and_fixture_disabled() if capture else nullcontext()
    with context:
        print(line, file=sys.stdout, flush=True)


async def _heartbeat(
    request: pytest.FixtureRequest, *, started: float, log_path: Path,
    seconds: float = 20.0,
) -> None:
    """Observe waiting time and log bytes; not proof the SDK is progressing."""
    while True:
        await asyncio.sleep(seconds)
        try:
            stream_bytes = log_path.stat().st_size
        except OSError:
            stream_bytes = 0
        _progress(
            request,
            "AGENT_WAITING",
            elapsed_s=int(time.monotonic() - started),
            stream_bytes=stream_bytes,
        )


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        check=True, timeout=30,
    )
    return proc.stdout.strip()


def _recorded_answer(path: Path) -> dict:
    turns = json.loads(path.read_text(encoding="utf-8"))
    answers: list[dict] = []
    for turn in turns:
        for step in turn.get("turn", {}).get("steps", []):
            raw = (step.get("message") or {}).get("text")
            if not isinstance(raw, str) or '"row_reviews"' not in raw or '"verdict"' not in raw:
                continue
            try:
                answers.append(json.loads(raw[raw.index("{"):raw.rindex("}") + 1]))
            except (ValueError, json.JSONDecodeError):
                continue
    if len(answers) != 1:
        raise AssertionError("REPLAY_AUDIT_AMBIGUOUS: expected exactly one recorded audit")
    answer = answers[0]
    assert answer["verdict"] == "SCENARIO_TEST_COVERAGE_FAIL"
    assert len(answer["gaps"]) == 2
    assert "source_digest" not in answer, "Historical audit must not be rebased into fresh evidence"
    return answer


def _real_replay_state(repo: Path, commit_sha: str, *, capture_execution_id: str) -> dict:
    run_folder = repo / "opsx/opsx_runtime/run_dialogs" / CHANGE_ID / RECORDED_RUN
    audit = _recorded_answer(run_folder / "scenario_coverage_verify/conversation.json")
    receipt_path = repo / "openspec/changes" / CHANGE_ID / "verify/IMPL-VERIFY-RECEIPT.json"
    impl = json.loads(receipt_path.read_text(encoding="utf-8"))["answer"]
    run_log = (run_folder / "run.log").read_text(encoding="utf-8")
    models = re.findall(r"agent=apply \| model=([^\s]+)", run_log)
    assert models, "Recorded Apply model identity absent"
    return {
        "change_id": CHANGE_ID,
        "model": {"id": models[-1]},
        # Новый SDK-вызов НЕ должен писать диалог под ID исторического аудита.
        # RECORDED_RUN остаётся только входной identity старого аудита.
        "run_uuid": capture_execution_id,
        "scenario_coverage_execution_id": RECORDED_RUN,
        "scenario_coverage_verdict": audit["verdict"],
        "scenario_coverage_answer": audit,
        "answer": impl,
        "repository_path": str(repo),
        "workspace": str(repo),
        "scenario_replay_only": True,
        "replay_commit_sha": commit_sha,
        "scenario_test_repair_authorized": True,
        "isolated_workspace": True,  # Disposable clone, NOT OS-level sandbox
    }


def _fixture_output_dir(source: Path) -> Path:
    """Raw capture inbox; promote after review to Git-tracked opsx/tests/recordings."""
    return source / "opsx/tests/inbox"


def _reserve_capture_dir(inbox: Path) -> tuple[Path, str]:
    """Создать отдельный каталог диалога без возможности перезаписи старого.

    uuid4 служит новым execution ID, а mkdir(exist_ok=False) — атомарным
    барьером от коллизии. Никогда не открываем каталог предыдущего запуска.
    При коллизии FAIL, без fallback на существующий каталог.
    """
    agent_inbox = inbox / "scenario_test_repair"
    agent_inbox.mkdir(parents=True, exist_ok=True)
    execution_id = f"scenario-repair-{uuid.uuid4().hex}"
    directory = agent_inbox / execution_id
    directory.mkdir(mode=0o700, exist_ok=False)
    return directory, execution_id


def _save_raw_agent_output(report_path: Path, text: str) -> tuple[Path, str]:
    """Preserve exact UTF-8 SDK final-answer bytes and their digest."""
    path = report_path.with_suffix(".assistant.txt")
    raw = text.encode("utf-8")
    # Эксклюзивное создание: даже повторный вызов по тому же имени не трёт
    # исходный SDK-текст, а останавливается на FileExistsError.
    with path.open("xb") as out:
        out.write(raw)
    return path, hashlib.sha256(raw).hexdigest()


def _write_report(path: Path, report: dict) -> None:
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


@pytest.mark.live_agent
@pytest.mark.asyncio
async def test_real_model_historical_replay_on_disposable_pinned_checkout(request):
    if str(request.config.option.markexpr or "").strip() != "live_agent":
        pytest.skip("Model calls require explicit pytest -m live_agent")

    started = time.monotonic()
    _progress(request, "REPLAY_START", mode="historical_recording")
    source = Path(request.config.rootpath).resolve()
    # Pin once to the exact source commit; never float while the model runs.
    pinned_sha = os.getenv("OPSX_SCENARIO_REPLAY_SHA") or _git(source, "rev-parse", "HEAD")
    # Persist raw SDK replies outside disposable checkout, in the source
    # repo's ignored opsx/tests/inbox. Only reviewed/promoted fixtures
    # under opsx/tests/recordings enter Git.
    inbox = _fixture_output_dir(source).resolve()
    _progress(request, "SOURCE_PINNED", commit=pinned_sha)
    # Резервируем каталог ДО импорта SDK и любых записей. Он принадлежит
    # одному запуску; записи прошлого запуска не открываются на запись.
    output_dir, capture_execution_id = _reserve_capture_dir(inbox)
    _progress(request, "RESULTS_DIRECTORY_READY", path=output_dir)
    report_path = output_dir / "scenario-repair-replay.json"
    source_status_before = _git(source, "status", "--porcelain")
    report = {
        "contract": "scenario_repair_replay_only.v1",
        "production_gate_evidence": False,
        "source_repo": str(source),
        "requested_commit_sha": pinned_sha,
        "recorded_run": RECORDED_RUN,
        "capture_execution_id": capture_execution_id,
        "change_id": CHANGE_ID,
        "model_call_status": "NOT_ATTEMPTED",
        "candidate_status": "NOT_RUN",
    }

    # Import SDK only inside the explicitly selected live test.
    _progress(request, "SDK_IMPORT_BEGIN")
    try:
        from agents.repair.scenario_test_repair.pinned_checkout import create_pinned_checkout
        from agents.repair.scenario_test_repair.agent import ScenarioTestRepairAgent
        from agents.verify.scenario_coverage_verify.input_pack import (
            assess_plan_materialization, load_scenario_coverage_plan_bundle,
        )
        from workflows.repair.repair_adapter_port import RepairAdapter
        _progress(request, "SDK_IMPORT_DONE", elapsed_s=int(time.monotonic() - started))
    except ImportError as exc:
        report["error"] = (
            f"REPLAY_RUNTIME_DEPENDENCY_MISSING: {type(exc).__name__}: {exc}"
        )
        _write_report(report_path, report)
        pytest.fail(report["error"], pytrace=False)

    class ReplayOnlyAgent(ScenarioTestRepairAgent):
        """Different *test* admission policy; production default is untouched."""

        run = ScenarioTestRepairAgent.run

        def _preflight_problem(self, state, *, repo_root, change_id):
            if state.get("scenario_replay_only") is not True:
                return "REPLAY_NOT_EXPLICIT"
            if not state.get("scenario_coverage_answer", {}).get("gaps"):
                return "REPLAY_NO_GAPS"
            if state.get("scenario_coverage_verdict") != "SCENARIO_TEST_COVERAGE_FAIL":
                return "REPLAY_NOT_FAILED_AUDIT"
            if state.get("replay_commit_sha") != _git(repo_root, "rev-parse", "HEAD"):
                return "REPLAY_COMMIT_MISMATCH"
            if state.get("repository_path") != str(repo_root):
                return "REPLAY_REPOSITORY_MISMATCH"
            plan = load_scenario_coverage_plan_bundle(change_id, repo_root=repo_root)
            issue = assess_plan_materialization(plan)
            if issue:
                return f"REPLAY_PLAN_INVALID: {issue}"
            plan_id = str((plan.get("plan_receipt") or {}).get("PLAN_EVIDENCE_ID") or "")
            if plan_id != state["scenario_coverage_answer"].get("plan_evidence_id"):
                return "REPLAY_PLAN_RECEIPT_MISMATCH"
            # Historical audit intentionally has NO source_digest.
            return None

    class ObservedAdapter(RepairAdapter):
        """Test-only adapter wrapper; no mutation of production SDK adapter."""

        async def _run_turn(
            self,
            state,
            *,
            prompt,
            tools,
            payload_model=None,
            resume_agent_id=None,
            read_scope=None,
        ):
            sdk_started = time.monotonic()
            _progress(
                request, "SDK_BRIDGE_BEGIN",
                model=(state.get("model") or {}).get("id", "?"),
            )
            try:
                return await super()._run_turn(
                    state,
                    prompt=prompt,
                    tools=tools,
                    payload_model=payload_model,
                    resume_agent_id=resume_agent_id,
                )
            finally:
                _progress(
                    request, "SDK_BRIDGE_RETURN_OR_ABORT",
                    elapsed_s=int(time.monotonic() - sdk_started),
                )

    checkout = None
    result = None
    try:
        _progress(request, "CHECKOUT_CREATE_BEGIN", sha=pinned_sha)
        checkout = create_pinned_checkout(source, pinned_sha)
        _progress(request, "CHECKOUT_CREATE_DONE", seconds=int(time.monotonic() - started))
        with checkout:
            repo = checkout.path
            _progress(request, "RECORDED_CONTEXT_LOAD_BEGIN")
            state = _real_replay_state(
                repo, pinned_sha, capture_execution_id=capture_execution_id,
            )
            _progress(
                request, "RECORDED_CONTEXT_READY",
                gaps=len(state["scenario_coverage_answer"]["gaps"]),
                model=state["model"]["id"],
            )
            report["recorded_gap_ids"] = [
                g["scenario_id"] for g in state["scenario_coverage_answer"]["gaps"]
            ]
            report["model"] = state["model"]
            report["actual_checkout_sha"] = _git(repo, "rev-parse", "HEAD")
            state["stream_log"] = str(output_dir / (report_path.stem + ".ndjson"))
            state["apply_stream_log"] = state["stream_log"]
            report["model_call_status"] = "UNPROVEN_IN_FLIGHT"
            _write_report(report_path, report)
            _progress(request, "AGENT_CALL_BEGIN", elapsed_s=int(time.monotonic() - started))
            adapter = ObservedAdapter(timeout_seconds=7200)
            agent_started = time.monotonic()
            heartbeat_task = asyncio.create_task(
                _heartbeat(
                    request, started=agent_started,
                    log_path=Path(state["stream_log"]),
                )
            )
            try:
                result = await ReplayOnlyAgent(adapter).run(state, repository_path=repo)
            finally:
                heartbeat_task.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat_task
            _progress(
                request, "AGENT_CALL_RETURNED",
                status=result.get("scenario_test_repair_status", "UNKNOWN"),
                seconds=int(time.monotonic() - agent_started),
                sdk_receipt=bool(adapter.last_result),
            )
            report["candidate_status"] = result.get("scenario_test_repair_status", "UNKNOWN")
            report["answer"] = result.get("scenario_test_repair_answer")
            report["changed_paths"] = result.get("scenario_test_repair_changed_paths")
            report["independent_verified"] = False
            report["model_call_status"] = (
                "SDK_RECEIPTED" if adapter.last_result is not None else "UNPROVEN"
            )
            if adapter.last_result is not None:
                report["sdk_run_id"] = adapter.last_result.run_id
                report["usage"] = adapter.last_result.usage
                report["cost"] = adapter.last_result.cost
                raw_path, raw_sha256 = _save_raw_agent_output(
                    report_path, adapter.last_result.text,
                )
                report["assistant_output"] = str(raw_path)
                report["assistant_sha256"] = raw_sha256
            # Conversation JSON and stream NDJSON are written alongside the
            # report by the SDK bridge; they survive checkout cleanup.
            conversation_path = Path(state["stream_log"]).with_suffix(".conversation.json")
            report["conversation_output"] = str(conversation_path)
            report["stream_output"] = state["stream_log"]
            try:
                conversation = json.loads(conversation_path.read_text(encoding="utf-8"))
                report["recording_complete"] = (
                    adapter.last_result is not None
                    and isinstance(conversation, list)
                    and bool(conversation)
                )
            except (OSError, ValueError):
                report["recording_complete"] = False
            diff = subprocess.run(
                ["git", "-C", str(repo), "diff", "--", "tests"],
                capture_output=True, text=True, check=False, timeout=30,
            )
            diff_path = report_path.with_suffix(".tests.diff")
            # Дифф предыдущего запуска никогда не заменяется.
            with diff_path.open("x", encoding="utf-8") as out:
                out.write(diff.stdout)
            report["full_test_diff"] = str(diff_path)
            report["test_diff"] = diff.stdout[-15000:]
            _progress(
                request, "TEST_DIFF_SAVED",
                diff_chars=len(report["test_diff"]),
                elapsed_s=int(time.monotonic() - started),
            )
            _progress(request, "CHECKOUT_CLEANUP_BEGIN")
    except BaseException as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        if report.get("model_call_status") == "UNPROVEN_IN_FLIGHT":
            report["model_call_status"] = "UNKNOWN_AFTER_INTERRUPTION"
        _progress(
            request, "REPLAY_ABORTED",
            exception=type(exc).__name__,
            sdk_state=report.get("model_call_status"),
        )
        raise
    finally:
        _progress(request, "CLEANUP_CHECK_BEGIN")
        report["checkout_removed"] = checkout is None or not checkout.path.parent.exists()
        report["source_head_after"] = _git(source, "rev-parse", "HEAD")
        report["source_git_status_unchanged"] = (
            _git(source, "status", "--porcelain") == source_status_before
        )
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        _write_report(report_path, report)
        _progress(
            request, "REPLAY_FINISHED",
            checkout_removed=report["checkout_removed"],
            elapsed_s=int(time.monotonic() - started),
            report=report_path,
        )

    assert report["checkout_removed"], "Disposable checkout was not removed"
    assert report.get("recording_complete") is True, (
        f"REPLAY_RECORDING_INCOMPLETE: inspect {report_path}"
    )
    assert report["source_head_after"] == pinned_sha, "Source HEAD changed during replay"
    assert report["source_git_status_unchanged"], "Source git status changed during replay"
    assert result is not None
    assert result.get("scenario_test_repair_status") == "CANDIDATE_PENDING_VERIFY", (
        f"Replay was not a candidate; inspect {report_path}"
    )
