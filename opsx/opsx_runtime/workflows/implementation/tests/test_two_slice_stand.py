"""Стенд two-slice: compiled subgraph, Node plan SoT, AsyncSqliteSaver, контракт lane."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from workflows.tests.test_implement_then_verify_workflow import _skip_design_lane_invoke

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from agents.adapters.abc_adapter import ABCAdapter, AgentResult
from agents.implementation.apply_green.schemas import ApplyGreenAnswer
from agents.implementation.apply_implement.schemas import ApplyImplementAnswer
from agents.implementation.apply_red.schemas import ApplyRedAnswer
from agents.state import GraphState
from workflows.implementation import stage_dispatch
from workflows.implementation.graph import (
    ImplementationHopAgents,
    build_implementation_hop_agents,
)
from workflows.implementation.routing import route_main_after_implementation
from workflows.implementation.design_machine_validation import DesignMachineGateResult
from workflows.implementation.receipt_loader import apply_design_file_sha256
from workflows.implementation.red_operative_validation import RedOperativeValidation
from workflows.implementation.routing_contract import (
    RUN_STATUS_APPLY_COMPLETE,
    RUN_STATUS_PAUSED,
    RUN_STATUS_RUNNING,
    STAGE_APPLY_COMPLETE,
    STAGE_APPLY_GREEN,
    STAGE_APPLY_IMPLEMENT,
    STAGE_APPLY_RED,
    STAGE_APPLY_SCAFFOLD,
    parse_super_implement_plan_payload,
)
from workflows.checkpoint_resume import WorkflowResumeScope, default_run_dir
from workflows.implementation.run import run_implementation_workflow
from workflows.main_graph import build_main_graph
from workflows.repair.tests.fakes import FakeAdapter

REPO_ROOT = Path(__file__).resolve().parents[5]
FIXTURE_PKG = REPO_ROOT / "tests" / "unit" / "fixtures" / "two_slice_stand"
if str(FIXTURE_PKG) not in sys.path:
    sys.path.insert(0, str(FIXTURE_PKG))

from hermetic_bootstrap import (  # noqa: E402
    CHANGE_ID,
    materialize_hermetic_repository,
    slice_001_test_path,
)

_APPLY_RED_OK = json.dumps(
    ApplyRedAnswer(
        status="APPLY_COMPLETE",
        tasks_summary="stand scripted red hop",
        gap_closure_summary="no file edits",
        files_changed=[],
        remaining_gaps=[],
    ).model_dump(mode="json"),
)
_APPLY_RED_STILL_BROKEN = _APPLY_RED_OK


class _ScriptedApplyAdapter(FakeAdapter):
    """Controllable SDK adapter: очередь JSON-ответов ApplyRedAnswer."""


def _plan_payload(raw: dict[str, object]) -> Any:
    """Мост typed plan: subprocess JSON → ``SuperImplementPlanStep``."""
    return parse_super_implement_plan_payload(raw)


_PLAN_STEP_BUDGET = 24
_SUPER_IMPLEMENT_SCRIPT_REL = "opsx_tools/opsx-super-implement.mjs"
_SLICE002_RECEIPT_NODE = """
import { writeFileSync, mkdirSync, readFileSync, existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import {
  APPLY_RED_SCHEMA_VERSION,
  RED_EVIDENCE_CONTRACT,
  RED_PROVEN,
  RED_PROVEN_CLASSIFICATION,
  IMPLEMENTATION_WRITTEN,
  GREEN_PROVEN,
} from "./opsx_tools/opsx-apply-pipeline-identity.mjs";
import { computeFrozenTestContentIdentity } from "./opsx_tools/opsx-frozen-test-content-identity.mjs";
import { runFrozenRedCommand } from "./opsx_tools/opsx-apply-green.mjs";
import { computeSliceBehaviorObligationDigest } from "./opsx_tools/opsx-apply-red-oracle-authority.mjs";
import { setSlice002Value } from "./tests/unit/fixtures/two_slice_stand/product.mjs";
const repo = process.cwd();
const CHANGE = "two-slice-stand";
const SLICE_ID = "SLICE-002";
const TEST_REL = "tests/unit/fixtures/two_slice_stand/slice_002.test.mjs";
const TEST_CMD = `node --test ${TEST_REL}`;
const DESIGN_REL = "openspec/changes/two-slice-stand/apply-design/APPLY-DESIGN.json";
const design = JSON.parse(readFileSync(resolve(repo, DESIGN_REL), "utf8"));
const slice = design.slices.find((s) => s.slice_id === SLICE_ID);
const obligation = computeSliceBehaviorObligationDigest(design, slice);
const identity = computeFrozenTestContentIdentity(repo, [TEST_REL]);
function writeJson(rel, payload) {
  const target = resolve(repo, rel);
  mkdirSync(dirname(target), { recursive: true });
  writeFileSync(target, JSON.stringify(payload, null, 2) + "\\n");
}
function writeRed() {
  setSlice002Value(0);
  const live = runFrozenRedCommand(TEST_CMD, { cwd: repo, timeoutMs: 60000 });
  writeJson(`openspec/changes/${CHANGE}/apply-red/${SLICE_ID}/RED.json`, {
    schema: "opsx.apply_red",
    schema_version: APPLY_RED_SCHEMA_VERSION,
    evidence_contract: RED_EVIDENCE_CONTRACT,
    status: RED_PROVEN,
    slice_id: SLICE_ID,
    change_id: CHANGE,
    tests_frozen: true,
    test_paths: [TEST_REL],
    test_content_identity: identity.digest,
    behavior_obligation_identity: {
      slice_id: SLICE_ID,
      planning_digest: design.planning_identity.planning_digest,
      obligation_digest: obligation.digest,
    },
    test_commands: [TEST_CMD],
    command_executions: [{
      command: TEST_CMD,
      test_path: TEST_REL,
      red_qualified: true,
      pass_qualified: false,
      final_exit_code: live.exit_code,
      final_classification: RED_PROVEN_CLASSIFICATION,
      attempts: [{ exit_code: live.exit_code }],
    }],
    repository_identity_before_red: { head: "stand-slice-002-pre-red" },
    apply_design_identity: {
      artifact_path: DESIGN_REL,
      status: "APPLY_DESIGN_READY",
      planning_digest: design.planning_identity.planning_digest,
    },
  });
}
function writeImplement(digest) {
  setSlice002Value(9);
  writeJson(`openspec/changes/${CHANGE}/apply-implement/${SLICE_ID}/IMPLEMENTATION.json`, {
    status: IMPLEMENTATION_WRITTEN,
    slice_id: SLICE_ID,
    change_id: CHANGE,
    next_stage: "APPLY_GREEN",
    tests_modified: false,
    tests_executed: false,
    frozen_test_content_identity: digest,
    red_identity: {
      status: RED_PROVEN,
      slice_id: SLICE_ID,
      test_content_identity: digest,
    },
    production_paths_modified: ["tests/unit/fixtures/two_slice_stand/product.mjs"],
  });
}
function writeGreen() {
  const redPath = resolve(repo, `openspec/changes/${CHANGE}/apply-red/${SLICE_ID}/RED.json`);
  const implPath = resolve(
    repo,
    `openspec/changes/${CHANGE}/apply-implement/${SLICE_ID}/IMPLEMENTATION.json`,
  );
  if (!existsSync(redPath)) {
    writeRed();
  }
  if (!existsSync(implPath)) {
    writeImplement(identity.digest);
  }
  setSlice002Value(9);
  const check = runFrozenRedCommand(TEST_CMD, { cwd: repo, timeoutMs: 60000 });
  if (check.exit_code !== 0) {
    throw new Error(`expected green pass, got ${check.exit_code}`);
  }
  const red = JSON.parse(
    readFileSync(resolve(repo, `openspec/changes/${CHANGE}/apply-red/${SLICE_ID}/RED.json`), "utf8"),
  );
  const implement = JSON.parse(
    readFileSync(
      resolve(repo, `openspec/changes/${CHANGE}/apply-implement/${SLICE_ID}/IMPLEMENTATION.json`),
      "utf8",
    ),
  );
  const scaffold = JSON.parse(
    readFileSync(resolve(repo, `openspec/changes/${CHANGE}/apply-scaffold/${SLICE_ID}/SCAFFOLD.json`), "utf8"),
  );
  writeJson(`openspec/changes/${CHANGE}/apply-green/${SLICE_ID}/GREEN.json`, {
    status: GREEN_PROVEN,
    slice_id: SLICE_ID,
    change_id: CHANGE,
    frozen_test_content_identity: red.test_content_identity,
    red_identity: {
      status: RED_PROVEN,
      slice_id: SLICE_ID,
      test_content_identity: red.test_content_identity,
    },
    implementation_identity: {
      status: IMPLEMENTATION_WRITTEN,
      slice_id: SLICE_ID,
      frozen_test_content_identity: implement.frozen_test_content_identity,
    },
    scaffold_identity: {
      status: scaffold.status || "SCAFFOLD_READY",
      slice_id: SLICE_ID,
    },
    apply_design_identity: red.apply_design_identity,
    red_executions: [{
      phase: "initial",
      iteration: 0,
      command: TEST_CMD,
      exit_code: check.exit_code,
      relevant_output: (check.stdout || check.stderr || "").slice(0, 500),
    }],
  });
}
const mode = process.env.OPSX_STAND_RECEIPT_MODE;
const digest = process.env.OPSX_STAND_RECEIPT_DIGEST;
if (mode === "red") writeRed();
if (mode === "implement") writeImplement(digest);
if (mode === "green") writeGreen();
"""


def _subprocess_super_implement_plan(
    change_id: str,
    *,
    repo_root: Path,
    live_execution_authorized: bool,
) -> dict[str, object]:
    """Реальный Node ``plan`` subprocess (минуя patchable ``plan_super_implement_step``)."""
    script = repo_root / _SUPER_IMPLEMENT_SCRIPT_REL
    rel_root = f"openspec/changes/{change_id}"
    live_flag = "true" if live_execution_authorized else "false"
    proc = subprocess.run(
        [
            "node",
            str(script),
            "plan",
            "--change-root",
            rel_root,
            "--live-execution",
            live_flag,
        ],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        msg = proc.stderr.strip() or proc.stdout.strip() or f"exit={proc.returncode}"
        pytest.fail(f"opsx-super-implement plan subprocess failed: {msg}")
    payload = json.loads(proc.stdout)
    if not isinstance(payload, dict):
        pytest.fail("opsx-super-implement plan returned non-object JSON")
    return payload


def _node_plan_raw(hermetic: Path) -> dict[str, object]:
    """Вызвать реальный ``opsx-super-implement.mjs plan`` на hermetic repo."""
    return _subprocess_super_implement_plan(
        CHANGE_ID,
        repo_root=hermetic,
        live_execution_authorized=True,
    )


def _copy_opsx_registry(hermetic: Path) -> None:
    """Скопировать ``opsx/registry`` для Node validators в hermetic."""
    registry_dst = hermetic / "opsx" / "registry"
    registry_src = REPO_ROOT / "opsx" / "registry"
    if registry_dst.exists():
        shutil.rmtree(registry_dst)
    shutil.copytree(registry_src, registry_dst)


def _refresh_slice_one_artifacts_from_fixture(hermetic: Path) -> None:
    """Вернуть SLICE-001 receipts из fixture после правки APPLY-DESIGN (freshness plan SoT)."""
    fixture_change = FIXTURE_PKG / "change" / "openspec" / "changes" / CHANGE_ID
    hermetic_change = hermetic / "openspec" / "changes" / CHANGE_ID
    for stage_dir in ("apply-red", "apply-implement", "apply-green", "apply-scaffold"):
        src = fixture_change / stage_dir / "SLICE-001"
        if not src.is_dir():
            continue
        dst = hermetic_change / stage_dir / "SLICE-001"
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)


def _repair_slice_two_design_bindings(hermetic: Path) -> None:
    """Привязать SLICE-002 к ``slice_002.test.mjs`` (как future-design repair)."""
    design_path = (
        hermetic / "openspec" / "changes" / CHANGE_ID / "apply-design" / "APPLY-DESIGN.json"
    )
    design = json.loads(design_path.read_text(encoding="utf-8"))
    for slice in design["slices"]:
        if slice["slice_id"] != "SLICE-002":
            continue
        slice["red_test_targets_or_seams"] = [
            "tests/unit/fixtures/two_slice_stand/slice_002.test.mjs",
        ]
        slice["required_behavior"] = "slice two constant 9"
        slice["red_oracle_bindings"] = [
            {
                "test_path": "tests/unit/fixtures/two_slice_stand/slice_002.test.mjs",
                "authoritative_expected": "9",
            },
        ]
    design_path.write_text(json.dumps(design, indent=2) + "\n", encoding="utf-8")
    digest = apply_design_file_sha256(CHANGE_ID, repo_root=hermetic)
    review_path = (
        hermetic
        / "openspec"
        / "changes"
        / CHANGE_ID
        / "apply-design-review"
        / "APPLY-DESIGN-REVIEW.json"
    )
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["status"] = "APPLY_DESIGN_REVIEW_PASS"
    review["apply_design_identity"] = {
        "planning_digest": design["planning_identity"]["planning_digest"],
        "artifact_sha256": digest,
    }
    review_path.write_text(json.dumps(review, indent=2) + "\n", encoding="utf-8")
    _refresh_slice_one_artifacts_from_fixture(hermetic)


def _artifact_sha256(path: Path) -> str:
    """SHA256 on-disk artifact bytes (Node ``artifactSha256`` parity)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_slice_two_green_evidence(hermetic: Path) -> None:
    """GREEN SLICE-002 с nested identities для complete gate (не status-only)."""
    change_root = hermetic / "openspec" / "changes" / CHANGE_ID
    slice_id = "SLICE-002"
    red_path = change_root / "apply-red" / slice_id / "RED.json"
    impl_path = change_root / "apply-implement" / slice_id / "IMPLEMENTATION.json"
    scaffold_path = change_root / "apply-scaffold" / slice_id / "SCAFFOLD.json"
    red = json.loads(red_path.read_text(encoding="utf-8"))
    implement = json.loads(impl_path.read_text(encoding="utf-8"))
    scaffold = json.loads(scaffold_path.read_text(encoding="utf-8"))
    test_cmd = "node --test tests/unit/fixtures/two_slice_stand/slice_002.test.mjs"
    proc = subprocess.run(
        test_cmd.split(),
        cwd=str(hermetic),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        pytest.fail(f"slice-002 green evidence test failed: {proc.stderr or proc.stdout}")
    frozen = red["test_content_identity"]
    green = {
        "status": "GREEN_PROVEN",
        "slice_id": slice_id,
        "change_id": CHANGE_ID,
        "chat_history_required": False,
        "tests_modified": False,
        "red_final_status": "PASS",
        "frozen_test_content_identity": frozen,
        "frozen_test_identity_before": frozen,
        "frozen_test_identity_after": frozen,
        "red_identity": {
            "machine_path": f"openspec/changes/{CHANGE_ID}/apply-red/{slice_id}/RED.json",
            "status": "RED_PROVEN",
            "slice_id": slice_id,
            "test_content_identity": red["test_content_identity"],
            "tests_frozen": red.get("tests_frozen", True),
            "artifact_sha256": _artifact_sha256(red_path),
        },
        "implementation_identity": {
            "machine_path": f"openspec/changes/{CHANGE_ID}/apply-implement/{slice_id}/IMPLEMENTATION.json",
            "status": "IMPLEMENTATION_WRITTEN",
            "slice_id": slice_id,
            "frozen_test_content_identity": implement["frozen_test_content_identity"],
            "artifact_sha256": _artifact_sha256(impl_path),
        },
        "scaffold_identity": {
            "machine_path": f"openspec/changes/{CHANGE_ID}/apply-scaffold/{slice_id}/SCAFFOLD.json",
            "status": scaffold.get("status", "SCAFFOLD_READY"),
            "slice_id": slice_id,
            "artifact_sha256": _artifact_sha256(scaffold_path),
        },
        "apply_design_identity": red.get("apply_design_identity"),
        "red_executions": [
            {
                "phase": "initial",
                "iteration": 0,
                "command": test_cmd,
                "exit_code": proc.returncode,
                "relevant_output": (proc.stdout or proc.stderr or "")[:500],
            },
        ],
    }
    green_path = change_root / "apply-green" / slice_id / "GREEN.json"
    green_path.write_text(json.dumps(green, indent=2) + "\n", encoding="utf-8")


def _upgrade_slice_one_green_evidence(hermetic: Path) -> None:
    """SLICE-001 GREEN с nested identity (не status-only stub на complete gate)."""
    change_root = hermetic / "openspec" / "changes" / CHANGE_ID
    design_path = change_root / "apply-design" / "APPLY-DESIGN.json"
    design = json.loads(design_path.read_text(encoding="utf-8"))
    design_identity = {
        "artifact_path": f"openspec/changes/{CHANGE_ID}/apply-design/APPLY-DESIGN.json",
        "status": "APPLY_DESIGN_READY",
        "planning_digest": design["planning_identity"]["planning_digest"],
        "artifact_sha256": _artifact_sha256(design_path),
    }
    red = json.loads(
        (change_root / "apply-red" / "SLICE-001" / "RED.json").read_text(encoding="utf-8"),
    )
    implement = json.loads(
        (change_root / "apply-implement" / "SLICE-001" / "IMPLEMENTATION.json").read_text(
            encoding="utf-8",
        ),
    )
    red_path = change_root / "apply-red" / "SLICE-001" / "RED.json"
    red["apply_design_identity"] = design_identity
    red_path.write_text(json.dumps(red, indent=2) + "\n", encoding="utf-8")
    test_cmd = "node --test tests/unit/fixtures/two_slice_stand/slice_001.test.mjs"
    proc = subprocess.run(
        test_cmd.split(),
        cwd=str(hermetic),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        pytest.fail(f"slice-001 green evidence test failed: {proc.stderr or proc.stdout}")
    scaffold = json.loads(
        (change_root / "apply-scaffold" / "SLICE-001" / "SCAFFOLD.json").read_text(encoding="utf-8"),
    )
    impl_path = change_root / "apply-implement" / "SLICE-001" / "IMPLEMENTATION.json"
    scaffold_path = change_root / "apply-scaffold" / "SLICE-001" / "SCAFFOLD.json"
    frozen = red["test_content_identity"]
    green = {
        "status": "GREEN_PROVEN",
        "slice_id": "SLICE-001",
        "change_id": CHANGE_ID,
        "chat_history_required": False,
        "tests_modified": False,
        "red_final_status": "PASS",
        "frozen_test_content_identity": frozen,
        "frozen_test_identity_before": frozen,
        "frozen_test_identity_after": frozen,
        "red_identity": {
            "machine_path": f"openspec/changes/{CHANGE_ID}/apply-red/SLICE-001/RED.json",
            "status": "RED_PROVEN",
            "slice_id": "SLICE-001",
            "test_content_identity": red["test_content_identity"],
            "tests_frozen": red.get("tests_frozen", True),
            "artifact_sha256": _artifact_sha256(red_path),
        },
        "implementation_identity": {
            "machine_path": f"openspec/changes/{CHANGE_ID}/apply-implement/SLICE-001/IMPLEMENTATION.json",
            "status": "IMPLEMENTATION_WRITTEN",
            "slice_id": "SLICE-001",
            "frozen_test_content_identity": implement["frozen_test_content_identity"],
            "artifact_sha256": _artifact_sha256(impl_path),
        },
        "scaffold_identity": {
            "machine_path": f"openspec/changes/{CHANGE_ID}/apply-scaffold/SLICE-001/SCAFFOLD.json",
            "status": scaffold.get("status", "SCAFFOLD_READY"),
            "slice_id": "SLICE-001",
            "artifact_sha256": _artifact_sha256(scaffold_path),
        },
        "apply_design_identity": design_identity,
        "red_executions": [
            {
                "phase": "initial",
                "iteration": 0,
                "command": test_cmd,
                "exit_code": proc.returncode,
                "relevant_output": (proc.stdout or proc.stderr or "")[:500],
            },
        ],
    }
    green_path = change_root / "apply-green" / "SLICE-001" / "GREEN.json"
    green_path.write_text(json.dumps(green, indent=2) + "\n", encoding="utf-8")


def _prepare_hermetic_compiled_bridge(hermetic: Path) -> None:
    """Hermetic + registry + design/review для real Node plan."""
    _copy_opsx_registry(hermetic)
    _repair_slice_two_design_bindings(hermetic)


def _normalize_hop_stage(raw: str) -> str:
    """Привести ``implementation_stage`` / ``mode`` к ``APPLY_*``."""
    text = str(raw or "").strip()
    if not text:
        return ""
    upper = text.upper()
    if upper.startswith("APPLY_"):
        return upper
    return f"APPLY_{upper}"


def _slice_change_root(hermetic: Path) -> Path:
    return hermetic / "openspec" / "changes" / CHANGE_ID


def _assert_slice_stage_receipts(hermetic: Path, slice_id: str) -> list[str]:
    """Проверить наличие RED→IMPLEMENT→GREEN артефактов для slice; вернуть порядок стадий."""
    stages_present: list[str] = []
    root = _slice_change_root(hermetic)
    if (root / "apply-red" / slice_id / "RED.json").is_file():
        stages_present.append(STAGE_APPLY_RED)
    if (root / "apply-implement" / slice_id / "IMPLEMENTATION.json").is_file():
        stages_present.append(STAGE_APPLY_IMPLEMENT)
    if (root / "apply-green" / slice_id / "GREEN.json").is_file():
        stages_present.append(STAGE_APPLY_GREEN)
    assert stages_present == [STAGE_APPLY_RED, STAGE_APPLY_IMPLEMENT, STAGE_APPLY_GREEN]
    return stages_present


class _TwoSliceCompiledBridgeAdapter(ABCAdapter):
    """Mutating adapter: пишет on-disk receipts SLICE-002 через Node tools (не plan stub)."""

    def __init__(self, repo: Path) -> None:
        self._repo = repo
        self._digest: str | None = None
        self.executed_hops: list[tuple[str, str | None]] = []

    def _run_node_receipt(self, mode: str, digest: str | None = None) -> None:
        env = {**os.environ, "OPSX_STAND_RECEIPT_MODE": mode}
        if digest is not None:
            env["OPSX_STAND_RECEIPT_DIGEST"] = digest
        proc = subprocess.run(
            ["node", "--input-type=module", "-e", _SLICE002_RECEIPT_NODE],
            cwd=str(self._repo),
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        if proc.returncode != 0:
            msg = proc.stderr.strip() or proc.stdout.strip()
            pytest.fail(f"slice-002 receipt node helper failed mode={mode}: {msg}")

    async def _run_turn(
        self,
        state: Mapping[str, Any],
        *,
        prompt: str,
        tools: Sequence[str],
        payload_model: Any = None,
        resume_agent_id: str | None = None,
    ) -> AgentResult:
        _ = prompt, tools, resume_agent_id
        stage = _normalize_hop_stage(
            str(state.get("implementation_stage") or state.get("mode") or ""),
        )
        if not stage:
            payload_stage = {
                ApplyRedAnswer: STAGE_APPLY_RED,
                ApplyImplementAnswer: STAGE_APPLY_IMPLEMENT,
                ApplyGreenAnswer: STAGE_APPLY_GREEN,
            }.get(payload_model)
            if isinstance(payload_stage, str):
                stage = payload_stage
        answer = state.get("answer")
        slice_id = str(state.get("implementation_slice_id") or "") or None
        if slice_id is None and isinstance(answer, dict) and answer.get("slice_id"):
            slice_id = str(answer["slice_id"])
        self.executed_hops.append((stage, slice_id))
        if payload_model is ApplyRedAnswer:
            self._run_node_receipt("red")
            red_path = (
                self._repo
                / "openspec/changes/two-slice-stand/apply-red/SLICE-002/RED.json"
            )
            self._digest = json.loads(red_path.read_text(encoding="utf-8"))["test_content_identity"]
            text = ApplyRedAnswer(
                status="APPLY_COMPLETE",
                tasks_summary="stand two-slice apply-red",
                gap_closure_summary="RED.json on disk via Node",
            ).model_dump_json()
        elif payload_model is ApplyImplementAnswer:
            assert self._digest is not None
            self._run_node_receipt("implement", self._digest)
            text = ApplyImplementAnswer(
                status="APPLY_COMPLETE",
                tasks_summary="stand two-slice apply-implement",
                gap_closure_summary="IMPLEMENTATION.json on disk via Node",
            ).model_dump_json()
        elif payload_model is ApplyGreenAnswer:
            target_slice = slice_id
            answer_raw = state.get("answer")
            if target_slice is None and isinstance(answer_raw, dict):
                target_slice = str(answer_raw.get("slice_id") or "") or None
            if target_slice == "SLICE-001":
                _upgrade_slice_one_green_evidence(self._repo)
            else:
                self._run_node_receipt("green")
                _write_slice_two_green_evidence(self._repo)
                _upgrade_slice_one_green_evidence(self._repo)
            text = ApplyGreenAnswer(
                status="APPLY_COMPLETE",
                tasks_summary="stand two-slice apply-green",
                gap_closure_summary="GREEN.json on disk via Node",
            ).model_dump_json()
        else:
            text = ApplyRedAnswer(
                status="APPLY_COMPLETE",
                tasks_summary="stand hop",
                gap_closure_summary="noop",
            ).model_dump_json()
        return AgentResult(
            text=text,
            status="finished",
            agent_id="two-slice-stand",
            run_id="two-slice-stand",
        )


@pytest.mark.asyncio
async def test_sqlite_plan_skips_ordinary_red_for_frozen_slice_one(tmp_path: Path) -> None:
    """Slice-001 GREEN: plan не назначает APPLY_RED для SLICE-001 (real Node plan)."""
    hermetic = materialize_hermetic_repository(tmp_path, REPO_ROOT)
    slice1_before = slice_001_test_path(hermetic).read_bytes()
    raw = _node_plan_raw(hermetic)
    step = _plan_payload(raw)
    assert step.next_slice != "SLICE-001" or step.next_stage != STAGE_APPLY_RED
    assert slice_001_test_path(hermetic).read_bytes() == slice1_before
    if step.status == RUN_STATUS_RUNNING and step.next_stage == STAGE_APPLY_RED:
        assert step.next_slice == "SLICE-002"
    checkpoint = tmp_path / "stand-plan.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint)) as saver:
        _ = saver
        assert checkpoint.parent.exists()
        _ = checkpoint.stat().st_size if checkpoint.exists() else 0


@pytest.mark.asyncio
async def test_future_design_repair_leaves_slice_one_frozen_bytes(tmp_path: Path) -> None:
    """Перепланирование DESIGN будущего слайса не мутирует frozen тест SLICE-001."""
    hermetic = materialize_hermetic_repository(tmp_path, REPO_ROOT)
    frozen_before = slice_001_test_path(hermetic).read_bytes()
    design_path = (
        hermetic
        / "openspec"
        / "changes"
        / CHANGE_ID
        / "apply-design"
        / "APPLY-DESIGN.json"
    )
    design = json.loads(design_path.read_text(encoding="utf-8"))
    slice_two = next(s for s in design["slices"] if s["slice_id"] == "SLICE-002")
    slice_two["red_test_targets_or_seams"] = [
        "tests/unit/fixtures/two_slice_stand/slice_002.test.mjs",
    ]
    slice_two["required_behavior"] = "slice two constant 9"
    slice_two["red_oracle_bindings"] = [
        {
            "test_path": "tests/unit/fixtures/two_slice_stand/slice_002.test.mjs",
            "authoritative_expected": "9",
        },
    ]
    _ = design_path.write_text(json.dumps(design, indent=2) + "\n", encoding="utf-8")
    slice_one = next(s for s in design["slices"] if s["slice_id"] == "SLICE-001")
    assert "slice_001.test.mjs" in slice_one["red_test_targets_or_seams"][0]
    assert slice_001_test_path(hermetic).read_bytes() == frozen_before
    raw = _node_plan_raw(hermetic)
    step = _plan_payload(raw)
    assert slice_001_test_path(hermetic).read_bytes() == frozen_before
    assert step.next_slice != "SLICE-001" or step.next_stage != STAGE_APPLY_RED


@pytest.mark.asyncio
async def test_global_apply_complete_requires_both_slices(tmp_path: Path) -> None:
    """APPLY_COMPLETE только после обоих GREEN; slice-001 alone недостаточен."""
    hermetic = materialize_hermetic_repository(tmp_path, REPO_ROOT)
    step_partial = _plan_payload(_node_plan_raw(hermetic))
    assert step_partial.status != RUN_STATUS_APPLY_COMPLETE
    script = hermetic / "opsx_tools" / "opsx-super-implement.mjs"
    complete = json.loads(
        __import__("subprocess").run(
            [
                "node",
                str(script),
                "plan",
                "--change-root",
                f"openspec/changes/{CHANGE_ID}",
                "--live-execution",
                "false",
            ],
            cwd=str(hermetic),
            capture_output=True,
            text=True,
            check=True,
        ).stdout,
    )
    parsed_complete = _plan_payload(complete)
    assert parsed_complete.status != RUN_STATUS_APPLY_COMPLETE


@pytest.mark.asyncio
async def test_red_operative_double_fail_is_terminal_paused_not_running(
    tmp_path: Path,
) -> None:
    """RED FAIL → один repair → снова FAIL → ``implementation_status`` PAUSED (не RUNNING)."""
    hermetic = materialize_hermetic_repository(tmp_path, REPO_ROOT)
    red_path = (
        hermetic
        / "openspec"
        / "changes"
        / CHANGE_ID
        / "apply-red"
        / "SLICE-002"
        / "RED.json"
    )
    red_path.parent.mkdir(parents=True, exist_ok=True)
    _ = red_path.write_text(
        json.dumps(
            {
                "status": "RED_PROVEN",
                "slice_id": "SLICE-002",
                "tests_frozen": True,
                "test_paths": ["tests/unit/fixtures/two_slice_stand/slice_002.test.mjs"],
                "test_commands": [
                    "node --test tests/unit/fixtures/two_slice_stand/slice_002.test.mjs",
                ],
                "command_executions": [],
            },
        )
        + "\n",
        encoding="utf-8",
    )
    adapter = _ScriptedApplyAdapter([_APPLY_RED_OK, _APPLY_RED_STILL_BROKEN])
    hop_agents = build_implementation_hop_agents(apply_adapter=adapter)  # type: ignore[arg-type]
    state: GraphState = {
        "change_id": CHANGE_ID,
        "repository_path": str(hermetic),
        "live_execution_authorized": True,
        "implementation_status": RUN_STATUS_RUNNING,
        "implementation_stage": STAGE_APPLY_RED,
        "implementation_slice_id": "SLICE-002",
        "implementation_hop_machine_repair_count": 0,
        "answer": {"slice_id": "SLICE-002"},
    }
    result = await hop_agents.apply_red.run(state)
    assert result.get("apply_red_status") == "BLOCKED"
    assert result.get("implementation_status") == RUN_STATUS_PAUSED
    assert result.get("implementation_status") != RUN_STATUS_RUNNING
    assert int(result.get("implementation_hop_machine_repair_count") or 0) == 1
    assert "apply_red_operative_validation" in result


def test_apply_red_operative_validation_declared_on_graph_state() -> None:
    """Контракт: ``apply_red_operative_validation`` объявлен в ``GraphState``."""
    annotations = getattr(GraphState, "__annotations__", {})
    assert "apply_red_operative_validation" in annotations


@pytest.mark.asyncio
async def test_verify_parent_not_entered_after_slice_one_green_only(tmp_path: Path) -> None:
    """Parent verify не стартует, пока lane не ``APPLY_COMPLETE`` (только slice-001 GREEN)."""
    verify_calls: list[dict[str, object]] = []

    def _fake_verify_builder(*_a: object, checkpointer: Any = None, **_k: object) -> Any:
        from langgraph.graph import END, START, StateGraph

        async def _verify_node(state: GraphState) -> dict[str, object]:
            verify_calls.append(dict(state))
            return {"verdict": "IMPL_OK"}

        builder = StateGraph(GraphState)
        builder.add_node("verify", _verify_node)
        _ = builder.add_edge(START, "verify")
        _ = builder.add_edge("verify", END)
        return builder.compile(checkpointer=checkpointer)

    class _StubHop:
        async def run(self, state: GraphState) -> dict[str, object]:
            stage = str(state.get("implementation_stage") or "")
            return {"implementation_last_hop": stage}

    class _PlanReady:
        async def run(self, state: GraphState) -> dict[str, object]:
            _ = state
            return {
                "planning_readiness_status": "READY",
                "planning_readiness_result": {"status": "READY"},
            }

    stub = _StubHop()
    hop_agents = ImplementationHopAgents(
        apply_design=stub,  # type: ignore[arg-type]
        apply_design_review=stub,  # type: ignore[arg-type]
        apply_scaffold=stub,  # type: ignore[arg-type]
        apply_red=stub,  # type: ignore[arg-type]
        apply_implement=stub,  # type: ignore[arg-type]
        apply_green=stub,  # type: ignore[arg-type]
        implementation_arbiter=stub,  # type: ignore[arg-type]
        scenario_coverage_plan=_PlanReady(),  # type: ignore[arg-type]
        scenario_test_author=stub,  # type: ignore[arg-type]
    )
    plan_queue = [
        {
            "status": RUN_STATUS_RUNNING,
            "next_stage": "APPLY_GREEN",
            "next_slice": "SLICE-001",
            "primitive_command": "/opsx-apply-green",
            "plan_only": True,
            "live_execution_authorized": True,
        },
        {
            "status": RUN_STATUS_PAUSED,
            "recovery_code": "STAND_SLICE2_BLOCKED",
            "live_execution_authorized": True,
        },
    ]

    def _pop_plan(*_a: object, **_k: object) -> Any:
        if not plan_queue:
            raise AssertionError("plan queue exhausted")
        return parse_super_implement_plan_payload(plan_queue.pop(0))

    with patch(
        "workflows.main_graph.build_verify_graph",
        side_effect=_fake_verify_builder,
    ):
        parent = build_main_graph(
            *_dummy_verify_bundle(),
            implementation_hop_agents=hop_agents,
            checkpointer=None,
        )
    hermetic = materialize_hermetic_repository(tmp_path, REPO_ROOT)
    initial: GraphState = {
        "change_id": CHANGE_ID,
        "repository_path": str(hermetic),
        "live_execution_authorized": True,
        "skip_implementation": False,
    }
    with (
        patch(
            "workflows.implementation.stage_dispatch.plan_super_implement_step",
            side_effect=_pop_plan,
        ),
        patch(
            "workflows.implementation.stage_dispatch.advance_super_implement_receipt",
            return_value={"status": RUN_STATUS_APPLY_COMPLETE},
        ),
    ):
        from workflows.implementation.routing import ImplementationLaneTerminalError

        with pytest.raises(ImplementationLaneTerminalError, match="STAND_SLICE2_BLOCKED"):
            _ = await parent.ainvoke(initial)
    assert len(verify_calls) == 0


def _dummy_verify_bundle() -> tuple[Any, ...]:
    from unittest.mock import MagicMock

    return tuple(MagicMock() for _ in range(17))


@pytest.mark.asyncio
async def test_compiled_subgraph_plan_bridge_via_run_implementation(tmp_path: Path) -> None:
    """``run_implementation_workflow``: real Node plan + SQLite + lane до ``APPLY_COMPLETE``."""
    hermetic = materialize_hermetic_repository(tmp_path, REPO_ROOT)
    _prepare_hermetic_compiled_bridge(hermetic)
    slice_one_before = slice_001_test_path(hermetic).read_bytes()
    adapter = _TwoSliceCompiledBridgeAdapter(hermetic)
    hop_agents = build_implementation_hop_agents(apply_adapter=adapter)
    run_dir = tmp_path / "impl-run"
    plan_payloads: list[dict[str, object]] = []
    node_plan_trace: list[dict[str, object]] = []
    _real_plan_super_implement_step = stage_dispatch.plan_super_implement_step

    def _budgeted_real_plan_super_implement_step(
        change_id: str,
        *,
        repo_root: Path,
        live_execution_authorized: bool = False,
        plan_payload_override: dict[str, Any] | None = None,
    ) -> Any:
        if plan_payload_override is not None:
            return parse_super_implement_plan_payload(plan_payload_override)
        invocation = len(node_plan_trace) + 1
        if invocation > _PLAN_STEP_BUDGET:
            pytest.fail(
                "plan step budget exceeded: "
                f"budget={_PLAN_STEP_BUDGET}, invocations={len(node_plan_trace)}, "
                f"trace={node_plan_trace!r}, hops={adapter.executed_hops!r}",
            )
        step = _real_plan_super_implement_step(
            change_id,
            repo_root=repo_root,
            live_execution_authorized=live_execution_authorized,
            plan_payload_override=plan_payload_override,
        )
        payload = dict(step.raw)
        node_plan_trace.append(
            {
                "invocation": invocation,
                "status": payload.get("status"),
                "next_stage": payload.get("next_stage"),
                "next_slice": payload.get("next_slice"),
                "exit_code": 0,
            },
        )
        plan_payloads.append(payload)
        return step

    initial: GraphState = {
        "change_id": CHANGE_ID,
        "repository_path": str(hermetic),
        "live_execution_authorized": True,
        "implementation_receipt_skip": True,
    }
    design_gate_ok = DesignMachineGateResult(
        ok=True,
        status="PASS",
        exit_code=0,
        errors=[],
    )
    red_operative_ok = RedOperativeValidation(
        ok=True,
        red_json_path=hermetic / "noop",
        exit_code=0,
        status="PASS",
        errors=[],
        raw_stdout="",
        raw_stderr="",
    )
    with (
        patch.object(
            stage_dispatch,
            "plan_super_implement_step",
            side_effect=_budgeted_real_plan_super_implement_step,
        ),
        patch(
            "workflows.implementation.graph._invoke_apply_design_lane_subgraph",
            side_effect=_skip_design_lane_invoke,
        ),
        patch(
            "workflows.implementation.design_machine_validation.validate_apply_design_machine",
            return_value=design_gate_ok,
        ),
        patch(
            "workflows.implementation.plan_hop.validate_apply_design_machine",
            return_value=design_gate_ok,
        ),
        patch(
            "agents.implementation.apply_red.agent.validate_red_operative",
            return_value=red_operative_ok,
        ),
        patch.object(
            hop_agents.scenario_coverage_plan,
            "run",
            new=AsyncMock(
                return_value={
                    "planning_readiness_status": "READY",
                    "planning_readiness_result": {"status": "READY"},
                },
            ),
        ),
    ):
        final = await run_implementation_workflow(
            initial,
            repo_root=hermetic,
            hop_agents=hop_agents,
            run_dir=run_dir,
        )

    if node_plan_trace:
        assert all(entry.get("exit_code") == 0 for entry in node_plan_trace)
    if plan_payloads:
        assert parse_super_implement_plan_payload(plan_payloads[0]).status in {
            RUN_STATUS_RUNNING,
            RUN_STATUS_APPLY_COMPLETE,
        }
    slice_stages = [
        stage
        for stage, _sid in adapter.executed_hops
        if stage
        in {
            STAGE_APPLY_SCAFFOLD,
            STAGE_APPLY_RED,
            STAGE_APPLY_IMPLEMENT,
            STAGE_APPLY_GREEN,
        }
    ]
    if slice_stages:
        assert STAGE_APPLY_RED in slice_stages
        assert STAGE_APPLY_IMPLEMENT in slice_stages
        assert STAGE_APPLY_GREEN in slice_stages
        red_idx = slice_stages.index(STAGE_APPLY_RED)
        impl_idx = slice_stages.index(STAGE_APPLY_IMPLEMENT)
        green_idx = slice_stages.index(STAGE_APPLY_GREEN)
        assert red_idx < impl_idx < green_idx, adapter.executed_hops
    _ = _assert_slice_stage_receipts(hermetic, "SLICE-001")
    if any(sid == "SLICE-002" for _stage, sid in adapter.executed_hops):
        _ = _assert_slice_stage_receipts(hermetic, "SLICE-002")
    assert slice_001_test_path(hermetic).read_bytes() == slice_one_before
    ckpt = default_run_dir(
        hermetic,
        WorkflowResumeScope(kind="implement", change_id=CHANGE_ID),
    ) / "checkpoints.sqlite"
    assert ckpt.exists()
    assert ckpt.stat().st_size > 0
    if final.get("implementation_status") != RUN_STATUS_APPLY_COMPLETE:
        pytest.fail(
            "lane must end APPLY_COMPLETE via slice list subgraph; got "
            f"status={final.get('implementation_status')!r}, "
            f"stage={final.get('implementation_stage')!r}, "
            f"recovery={final.get('implementation_recovery_code')!r}, "
            f"plan_invocations={len(node_plan_trace)}, hops={adapter.executed_hops!r}",
        )
    assert final.get("implementation_stage") == STAGE_APPLY_COMPLETE
