"""
Cursor Agent CLI adapter for sequential prompt invocations.

LEGACY / NON-CANONICAL transport for review/repair lanes (bugbot, security,
project-bug-hunt, verification). Canonical SDK execution for new process slices
is tools/temporal/cursor_execution_activity.py → CursorExecutor V0.1.

Uses the installed `agent` CLI with a single persisted chat session so the
repair hop can consume immediate-prior review output (bugbot R2 / security table).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config import REPAIR_PROMPT, REVIEW_PROMPT
from lanes import PROJECT_BUG_HUNT_LANE, make_security_lane
from model_config import AgentModelConfig


class CursorAdapterError(RuntimeError):
    """Base adapter failure."""


class CursorTransientError(CursorAdapterError):
    """Retryable infrastructure failure."""


class CursorSessionIntegrationGap(CursorAdapterError):
    """Real Cursor integration is not available on this host."""


def resolve_agent_executable() -> str:
    agent_path = shutil.which("agent")
    if not agent_path:
        raise CursorSessionIntegrationGap("agent CLI not found in PATH")
    return agent_path


_CHAT_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _parse_chat_id(output: str) -> str | None:
    for line in reversed(output.strip().splitlines()):
        candidate = line.strip()
        if _CHAT_ID_RE.match(candidate):
            return candidate
    return None


def create_agent_chat(*, timeout_s: int = 120) -> str:
    """Create a Cursor chat and return its id without waiting for CLI exit.

    On some cloud hosts `agent create-chat` prints the id promptly but keeps
    running; subprocess communicate() then hits timeout despite a valid id.
    """
    agent_path = resolve_agent_executable()
    if sys.platform == "win32" and agent_path.lower().endswith((".cmd", ".bat")):
        command = ["cmd", "/c", agent_path, "create-chat"]
    else:
        command = [agent_path, "create-chat"]
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    collected: list[str] = []
    deadline = time.time() + timeout_s
    try:
        while time.time() < deadline:
            if proc.stdout is None:
                break
            line = proc.stdout.readline()
            if line:
                collected.append(line)
                chat_id = _parse_chat_id(line)
                if chat_id:
                    if proc.poll() is None:
                        proc.kill()
                        proc.wait(timeout=5)
                    return chat_id
            elif proc.poll() is not None:
                break
            time.sleep(0.05)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
    finally:
        if proc.stdout is not None:
            tail = proc.stdout.read()
            if tail:
                collected.append(tail)

    chat_id = _parse_chat_id("".join(collected))
    if chat_id:
        return chat_id
    output = "".join(collected).strip()
    raise CursorSessionIntegrationGap(
        f"agent create-chat returned no chat id: {output[:200] or '<empty>'}"
    )


def _decode_subprocess_stream(data: bytes | None) -> str:
    if not data:
        return ""
    return data.decode("utf-8", errors="replace")


def run_agent_command(
    args: list[str],
    *,
    cwd: str | None = None,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    agent_path = resolve_agent_executable()
    if sys.platform == "win32" and agent_path.lower().endswith((".cmd", ".bat")):
        command = ["cmd", "/c", agent_path, *args]
    else:
        command = [agent_path, *args]
    proc = subprocess.run(
        command,
        capture_output=True,
        check=False,
        timeout=timeout,
        cwd=cwd,
    )
    return subprocess.CompletedProcess(
        args=proc.args,
        returncode=proc.returncode,
        stdout=_decode_subprocess_stream(proc.stdout),
        stderr=_decode_subprocess_stream(proc.stderr),
    )


@dataclass
class PromptInvocation:
    prompt: str
    raw_output: str
    exit_code: int
    session_id: str


@dataclass
class MockScenarioStep:
    prompt: str
    result: str
    transient_failures: int = 0


@dataclass
class MockScenario:
    name: str
    steps: list[MockScenarioStep] = field(default_factory=list)


class CursorAdapter(ABC):
  @abstractmethod
  def session_id(self) -> str:
      raise NotImplementedError

  @abstractmethod
  def execute_prompt(self, prompt: str, *, step: str | None = None) -> PromptInvocation:
      raise NotImplementedError

  def reset_session(self, *, reason: str | None = None) -> str:
      """Start a fresh Cursor chat for the next review attempt."""
      return self.session_id()


class MockCursorAdapter(CursorAdapter):
    """Deterministic mock that simulates separate Cursor prompt invocations."""

    def __init__(self, scenario: MockScenario, session_id: str = "mock-session") -> None:
        self._scenario = scenario
        self._session_id = session_id
        self._index = 0
        self._attempt_counts: dict[int, int] = {}
        self.history: list[PromptInvocation] = []

    def session_id(self) -> str:
        return self._session_id

    def reset_session(self, *, reason: str | None = None) -> str:
        suffix = f"-{reason}" if reason else "-reset"
        self._session_id = f"{self._session_id}{suffix}"
        return self._session_id

    def execute_prompt(self, prompt: str, *, step: str | None = None) -> PromptInvocation:
        if self._index >= len(self._scenario.steps):
            raise CursorAdapterError(
                f"Mock scenario '{self._scenario.name}' exhausted at prompt={prompt!r}"
            )

        step = self._scenario.steps[self._index]
        if step.prompt != prompt:
            raise CursorAdapterError(
                f"Mock expected prompt {step.prompt!r}, got {prompt!r} at step {self._index}"
            )

        attempts = self._attempt_counts.get(self._index, 0)
        self._attempt_counts[self._index] = attempts + 1

        if attempts < step.transient_failures:
            invocation = PromptInvocation(
                prompt=prompt,
                raw_output="TRANSIENT_ERROR: CLI connection lost",
                exit_code=1,
                session_id=self._session_id,
            )
            self.history.append(invocation)
            return invocation

        invocation = PromptInvocation(
            prompt=prompt,
            raw_output=step.result,
            exit_code=0,
            session_id=self._session_id,
        )
        self.history.append(invocation)
        self._index += 1
        return invocation


class RealCursorAdapter(CursorAdapter):
    """
    Real Cursor Agent CLI integration.

    Each `execute_prompt` is a separate `agent` CLI process bound to the same
    chat via `--resume <chat_id>`, preserving immediate-prior conversation
    semantics required by `/opsx-orch-bug-fix`.
    """

    def __init__(
        self,
        repo_path: str | Path,
        session_id: str | None = None,
        *,
        force: bool = True,
        approve_mcps: bool = True,
        timeout_seconds: int | None = None,
        model_config: AgentModelConfig | None = None,
    ) -> None:
        self.repo_path = str(Path(repo_path).resolve())
        self._session_id = session_id or self._create_chat()
        self.force = force
        self.approve_mcps = approve_mcps
        self.timeout_seconds = timeout_seconds
        self.model_config = model_config
        self.history: list[PromptInvocation] = []

    def session_id(self) -> str:
        return self._session_id

    def reset_session(self, *, reason: str | None = None) -> str:
        """Discard the current chat and create a new empty session."""
        self._session_id = self._create_chat()
        return self._session_id

    @staticmethod
    def probe(repo_path: str | Path | None = None) -> dict[str, Any]:
        """Collect local CLI capability evidence without running a long agent job."""
        repo = str(Path(repo_path or ".").resolve())
        evidence: dict[str, Any] = {
            "agent_cli_available": False,
            "agent_version": None,
            "create_chat_works": False,
            "resume_works": False,
            "print_json_works": False,
            "session_continuation": "UNKNOWN",
            "repo_probe": repo,
            "errors": [],
        }

        try:
            version = run_agent_command(["--version"], timeout=30)
            if version.returncode == 0:
                evidence["agent_cli_available"] = True
                evidence["agent_version"] = version.stdout.strip() or version.stderr.strip()
        except Exception as exc:  # noqa: BLE001
            evidence["errors"].append(f"agent --version failed: {exc}")
            return evidence

        try:
            chat_id = create_agent_chat(timeout_s=180)
            evidence["create_chat_works"] = bool(chat_id)
            if not evidence["create_chat_works"]:
                evidence["errors"].append("agent create-chat did not return a chat id")
                return evidence
        except Exception as exc:  # noqa: BLE001
            evidence["errors"].append(f"agent create-chat failed: {exc}")
            return evidence

        try:
            first = run_agent_command(
                [
                    "--print",
                    "--output-format",
                    "json",
                    "--trust",
                    "--workspace",
                    repo,
                    "--resume",
                    chat_id,
                    "Reply exactly: POC_ACK",
                ],
                timeout=120,
                cwd=repo,
            )
            evidence["resume_works"] = first.returncode == 0
            if first.stdout.strip():
                try:
                    payload = json.loads(first.stdout.strip().splitlines()[-1])
                    evidence["print_json_works"] = payload.get("subtype") == "success"
                except json.JSONDecodeError:
                    evidence["print_json_works"] = "POC_ACK" in first.stdout
        except Exception as exc:  # noqa: BLE001
            evidence["errors"].append(f"agent resume probe failed: {exc}")
            return evidence

        try:
            second = run_agent_command(
                [
                    "--print",
                    "--trust",
                    "--workspace",
                    repo,
                    "--resume",
                    chat_id,
                    "Reply exactly: POC_BANANA",
                ],
                timeout=120,
                cwd=repo,
            )
            evidence["session_continuation"] = (
                "WORKING" if second.returncode == 0 and "POC_BANANA" in second.stdout else "GAP"
            )
        except Exception as exc:  # noqa: BLE001
            evidence["errors"].append(f"agent continuation probe failed: {exc}")
            evidence["session_continuation"] = "GAP"

        return evidence

    def _create_chat(self) -> str:
        return create_agent_chat(timeout_s=180)

    def execute_prompt(self, prompt: str, *, step: str | None = None) -> PromptInvocation:
        args: list[str] = []
        if self.model_config is not None:
            cli_model = self.model_config.resolve_cli_model(step=step)
            if cli_model:
                args.extend(["--model", cli_model])
        args.extend(
            [
            "--print",
            "--output-format",
            "json",
            "--trust",
            "--workspace",
            self.repo_path,
            "--resume",
            self._session_id,
            prompt,
            ]
        )
        if self.force:
            args.insert(0, "--force")
        if self.approve_mcps:
            args.insert(0, "--approve-mcps")

        try:
            proc = run_agent_command(
                args,
                timeout=self.timeout_seconds,
                cwd=self.repo_path,
            )
        except subprocess.TimeoutExpired as exc:
            raise CursorTransientError(f"agent timed out for prompt={prompt!r}") from exc
        except OSError as exc:
            raise CursorTransientError(f"agent launch failed: {exc}") from exc

        raw_output = self._extract_output(proc.stdout, proc.stderr)
        invocation = PromptInvocation(
            prompt=prompt,
            raw_output=raw_output,
            exit_code=proc.returncode,
            session_id=self._session_id,
        )
        self.history.append(invocation)

        if proc.returncode != 0 and self._looks_transient(raw_output, proc.stderr):
            raise CursorTransientError(raw_output or proc.stderr)

        if proc.returncode != 0:
            raise CursorAdapterError(raw_output or proc.stderr or f"exit={proc.returncode}")

        return invocation

    @staticmethod
    def _extract_output(stdout: str | None, stderr: str | None) -> str:
        text = (stdout or "").strip()
        if not text:
            return (stderr or "").strip()
        for line in reversed(text.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and "result" in payload:
                return str(payload["result"])
        return text

    @staticmethod
    def _looks_transient(stdout: str | None, stderr: str | None) -> bool:
        combined = f"{stdout or ''}\n{stderr or ''}".lower()
        markers = (
            "connection",
            "timeout",
            "temporarily",
            "unavailable",
            "econnreset",
            "etimedout",
        )
        return any(marker in combined for marker in markers)


def build_five_findings_output() -> str:
    return """REVIEW_SCOPE: FULL_BRANCH_DIFF
BASE_REF: stage
HEAD_REF: feature/test
DIFF_STAT_LINES: 42

Bugbot found 5 findings

| Severity | Location | Finding |
| High | src/a.js:10 | Null dereference |
| High | src/b.js:20 | Race condition |
| Medium | src/c.js:30 | Missing validation |
| Medium | src/d.js:40 | Stale cache |
| Low | src/e.js:50 | Naming issue |
"""


def build_two_findings_output() -> str:
    return """REVIEW_SCOPE: FULL_BRANCH_DIFF
BASE_REF: stage
HEAD_REF: feature/test
DIFF_STAT_LINES: 12

Bugbot found 2 findings

| Severity | Location | Finding |
| High | src/a.js:10 | Null dereference |
| Medium | src/c.js:30 | Missing validation |
"""


def build_clean_output() -> str:
    return """REVIEW_SCOPE: FULL_BRANCH_DIFF
BASE_REF: stage
HEAD_REF: feature/test
DIFF_STAT_LINES: 0

Bugbot found no bugs."""


def build_bug_batch_clear_output() -> str:
    return """STATUS: BUG_BATCH_CLEAR
MISSION_STATUS: BUG_BATCH_CLEAR
BUG_BATCH_CLEAR: YES
REPAIRED_THIS_BATCH: YES
RUNNING_WORKER: none
UNRESOLVED:
  none
"""


def build_security_findings_output() -> str:
    return """Security review found 2 findings

| Severity | Location (file:line) | Finding |
| High | src/store/index.js:10 | Untrusted balance write |
| Medium | src/utils/applyClampedBalanceDelta.js:4 | Missing bound check |
"""


def build_security_two_findings_output() -> str:
    return """Security review found 1 findings

| Severity | Location (file:line) | Finding |
| Medium | src/utils/applyClampedBalanceDelta.js:4 | Missing bound check |
"""


def build_security_clean_output() -> str:
    return "Security review found no issues"


def build_security_batch_clear_output() -> str:
    return """STATUS: SECURITY_BATCH_CLEAR
SECURITY_BATCH_CLEAR: YES
RUNNING_WORKER: none
UNRESOLVED:
  none
"""


def build_hunt_findings_output() -> str:
    return """STATUS: HUNT_COMPLETE
MODE: READ_ONLY
COMMAND: /opsx-project-bug-hunt

PROJECT_BUG_HUNT_RECEIPT:
- findings_total: 1
- confirmed_production_bug_count: 1

FINDINGS:
### FINDING_ID: PBH-001
- TITLE: softProdNovelSaveGet hangs without AbortSignal
- LOC_LINK: src/api/softProdNovelSave.js:28
- LITERAL_CLAIM: bare fetch with no abort leaves bootstrap waiting forever
- PRODUCTION_CLASSIFICATION: High
- SEVERITY: High
"""


def build_hunt_second_findings_output() -> str:
    return """STATUS: HUNT_COMPLETE
MODE: READ_ONLY
COMMAND: /opsx-project-bug-hunt

PROJECT_BUG_HUNT_RECEIPT:
- findings_total: 1
- confirmed_production_bug_count: 1

FINDINGS:
### FINDING_ID: PBH-002
- TITLE: Remaining timeout edge case
- LOC_LINK: src/api/softProdNovelSave.js:40
- LITERAL_CLAIM: PUT path still missing AbortSignal
- PRODUCTION_CLASSIFICATION: Medium
- SEVERITY: Medium
"""


def build_hunt_clean_output() -> str:
    return """STATUS: HUNT_COMPLETE
MODE: READ_ONLY
COMMAND: /opsx-project-bug-hunt

PROJECT_BUG_HUNT_RECEIPT:
- findings_total: 0
- confirmed_production_bug_count: 0

FINDINGS:
"""


def build_hunt_orch_clear_output() -> str:
    return """STATUS: BUG_BATCH_CLEAR
MISSION_STATUS: BUG_BATCH_CLEAR
BUG_BATCH_CLEAR: YES
REPAIRED_THIS_BATCH: YES
OWNER_AGGREGATE:
  FIX_NOW:
    - PBH-001: FIXED
RUNNING_WORKER: none
UNRESOLVED:
  none
"""


def build_hunt_orch_no_repair_output() -> str:
    return """STATUS: PARTIAL_COMPLETE
MISSION_STATUS: PARTIAL_COMPLETE
BUG_BATCH_CLEAR: NO
REPAIRED_THIS_BATCH: NO
OWNER_AGGREGATE:
  FIX_NOW: (none)
RUNNING_WORKER: none
UNRESOLVED:
  none
"""


def build_hunt_orch_defer_only_clear_output() -> str:
    """Real-world stall case: BUG_BATCH_CLEAR after defer-only, no FIX_NOW."""
    return """STATUS: BUG_BATCH_CLEAR
MISSION_STATUS: BUG_BATCH_CLEAR
BUG_BATCH_CLEAR: YES
OWNER_AGGREGATE:
  FIX_NOW: (none — no MUST_FIX in admitted batch)
  DEFERRED_PRODUCT_BUGS:
    - PBH-002: DEFER_PRODUCT_BUG
RUNNING_WORKER: none
UNRESOLVED:
  none
"""


def build_scenario(name: str) -> MockScenario:
    if name == "scenario1":
        return MockScenario(
            name="scenario1",
            steps=[
                MockScenarioStep(REVIEW_PROMPT, build_five_findings_output()),
                MockScenarioStep(REPAIR_PROMPT, build_bug_batch_clear_output()),
                MockScenarioStep(REVIEW_PROMPT, build_two_findings_output()),
                MockScenarioStep(REPAIR_PROMPT, build_bug_batch_clear_output()),
                MockScenarioStep(REVIEW_PROMPT, build_clean_output()),
            ],
        )
    if name == "scenario2":
        steps: list[MockScenarioStep] = []
        for round_idx in range(10):
            raw = build_five_findings_output().replace("src/a.js", f"src/a{round_idx}.js")
            steps.append(
                MockScenarioStep(
                    REVIEW_PROMPT,
                    raw.replace("5 findings", f"5 findings round {round_idx + 1}"),
                )
            )
            steps.append(MockScenarioStep(REPAIR_PROMPT, build_bug_batch_clear_output()))
        steps.append(MockScenarioStep(REVIEW_PROMPT, build_two_findings_output()))
        return MockScenario(name="scenario2", steps=steps)
    if name == "scenario3":
        same = build_two_findings_output()
        return MockScenario(
            name="scenario3",
            steps=[
                MockScenarioStep(REVIEW_PROMPT, same),
                MockScenarioStep(REPAIR_PROMPT, build_bug_batch_clear_output()),
                MockScenarioStep(REVIEW_PROMPT, same),
                MockScenarioStep(REPAIR_PROMPT, build_bug_batch_clear_output()),
                MockScenarioStep(REVIEW_PROMPT, same),
            ],
        )
    if name == "scenario4":
        return MockScenario(
            name="scenario4",
            steps=[
                MockScenarioStep(
                    REVIEW_PROMPT,
                    build_five_findings_output(),
                    transient_failures=1,
                ),
                MockScenarioStep(REPAIR_PROMPT, build_bug_batch_clear_output()),
                MockScenarioStep(REVIEW_PROMPT, build_clean_output()),
            ],
        )
    if name == "security-scenario1":
        security = make_security_lane()
        return MockScenario(
            name="security-scenario1",
            steps=[
                MockScenarioStep(security.review_prompt, build_security_findings_output()),
                MockScenarioStep(security.repair_prompt, build_security_batch_clear_output()),
                MockScenarioStep(security.review_prompt, build_security_two_findings_output()),
                MockScenarioStep(security.repair_prompt, build_security_batch_clear_output()),
                MockScenarioStep(security.review_prompt, build_security_clean_output()),
            ],
        )
    if name == "project-bug-hunt-scenario1":
        hunt = PROJECT_BUG_HUNT_LANE
        return MockScenario(
            name="project-bug-hunt-scenario1",
            steps=[
                MockScenarioStep(hunt.review_prompt, build_hunt_findings_output()),
                MockScenarioStep(hunt.repair_prompt, build_hunt_orch_clear_output()),
                MockScenarioStep(hunt.review_prompt, build_hunt_second_findings_output()),
                MockScenarioStep(hunt.repair_prompt, build_hunt_orch_clear_output()),
                MockScenarioStep(hunt.review_prompt, build_hunt_clean_output()),
            ],
        )
    if name == "project-bug-hunt-no-repair":
        hunt = PROJECT_BUG_HUNT_LANE
        return MockScenario(
            name="project-bug-hunt-no-repair",
            steps=[
                MockScenarioStep(hunt.review_prompt, build_hunt_findings_output()),
                MockScenarioStep(hunt.repair_prompt, build_hunt_orch_no_repair_output()),
            ],
        )
    if name == "project-bug-hunt-defer-only":
        hunt = PROJECT_BUG_HUNT_LANE
        return MockScenario(
            name="project-bug-hunt-defer-only",
            steps=[
                MockScenarioStep(hunt.review_prompt, build_hunt_findings_output()),
                MockScenarioStep(hunt.repair_prompt, build_hunt_orch_defer_only_clear_output()),
            ],
        )
    raise ValueError(f"Unknown mock scenario: {name}")
