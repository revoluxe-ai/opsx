"""Deterministic command invocation preflight before any Cursor SDK call."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from .schemas import CursorExecutionRequest


@dataclass(frozen=True)
class CommandPreflightBlock:
    command_id: str
    contract_status: str
    code: str
    message: str
    result_text: str
    contract_path: str
    contract_sha256: str


_TOKEN_RE = re.compile(r"(?:^|\s)([A-Za-z0-9_-]+)=(?:\"([^\"]*)\"|(\S+))")


_EMPTY_ARGUMENT_RULES: dict[str, tuple[str, str, str, str]] = {
    "opsx-bug-intake-clear": (
        "BLOCKED_NEEDS_INPUT", "COMMAND_INPUT_REQUIRED",
        "no case, finding, or parseable intake evidence was supplied",
        "provide case=<id>, finding=<id>, or bounded intake evidence",
    ),
    "opsx-estimate": (
        "BLOCKED_NEEDS_MODE", "COMMAND_INPUT_REQUIRED",
        "change and MODE are required to select an estimate operation",
        "provide change=<id> MODE=FORECAST|ACTUALS",
    ),
    "opsx-intent-align": (
        "OWNER_CLARIFICATION_REQUIRED", "COMMAND_INPUT_REQUIRED",
        "no product intent or change binding was supplied",
        "provide request=<text> or change=<id>",
    ),
    "opsx-meta-add-command": (
        "BLOCKED_NEEDS_INPUT", "COMMAND_INPUT_REQUIRED",
        "COMMAND and PURPOSE are required",
        "provide COMMAND=<name> PURPOSE=<intent>",
    ),
    "opsx-meta-promote-process-lesson": (
        "BLOCKED_NEEDS_EVIDENCE", "COMMAND_EVIDENCE_REQUIRED",
        "no process lesson evidence packet was supplied",
        "provide a grounded FIXED/FIX_PLAN_READY or equivalent evidence packet",
    ),
    "opsx-risk": (
        "FAIL_CLOSED", "COMMAND_INPUT_REQUIRED",
        "mode and change are required for risk classification",
        "provide mode=planning|tasking change=<id>",
    ),
    "opsx-run-build": (
        "BLOCKED", "COMMAND_INPUT_REQUIRED",
        "build recipe or explicit bounded mode is required",
        "provide build_recipe=poetry_package output_dir=<path> or mode=bounded",
    ),
    "opsx-run-tests": (
        "BLOCKED", "COMMAND_INPUT_REQUIRED",
        "test execution mode is required",
        "provide mode=manual lane=pytest test_ids=<node-id>[,<node-id>...] or mode=bounded",
    ),
    "opsx-run-typecheck": (
        "MODE_NOT_BOUNDED", "COMMAND_INPUT_REQUIRED",
        "mode=bounded and change are required",
        "provide mode=bounded change=<id>",
    ),
    "opsx-security-review": (
        "BLOCKED", "COMMAND_INPUT_REQUIRED",
        "security review mode is required",
        "provide mode=report change=<id> revision=<40-hex-head> or mode=bounded",
    ),
    "opsx-security-triage": (
        "BLOCKED_NEEDS_INPUT", "COMMAND_INPUT_REQUIRED",
        "no security signal or source evidence was supplied",
        "provide the security signal/evidence and its source",
    ),
    "opsx-sherlock": (
        "BLOCKED_NEEDS_INPUT", "COMMAND_INPUT_REQUIRED",
        "a symptom or continuation identity is required",
        "provide a symptom, or sherlock_case=<id> mode=continue with evidence",
    ),
    "opsx-ts-design": (
        "FAIL_CLOSED", "COMMAND_INPUT_REQUIRED",
        "change is required for technical strategy design",
        "provide change=<id> [mode=draft|reconcile]",
    ),
    "opsx-ts-review": (
        "FAIL_CLOSED", "COMMAND_INPUT_REQUIRED",
        "change is required for technical strategy review",
        "provide change=<id>",
    ),
}


def _slash_command(prompt: str) -> tuple[str | None, str]:
    stripped = prompt.strip()
    if not stripped.startswith("/"):
        return None, stripped
    first, _, rest = stripped.partition(" ")
    command_id = first[1:]
    if not command_id:
        return None, stripped
    return command_id, rest.strip()


def _tokens(arguments: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for match in _TOKEN_RE.finditer(arguments):
        values[match.group(1)] = match.group(2) if match.group(2) is not None else match.group(3)
    return values


def _contract_identity(workspace: str, command_id: str) -> tuple[str, str] | None:
    rel = f".cursor/commands/{command_id}.md"
    path = Path(workspace) / ".cursor" / "commands" / f"{command_id}.md"
    if not path.is_file():
        return None
    return rel, hashlib.sha256(path.read_bytes()).hexdigest()


def _block(
    request: CursorExecutionRequest,
    *,
    command_id: str,
    status: str,
    code: str,
    why: str,
    next_action: str,
) -> CommandPreflightBlock:
    identity = _contract_identity(request.workspace, command_id)
    if identity is None:
        rel = f".cursor/commands/{command_id}.md"
        return CommandPreflightBlock(
            command_id=command_id,
            contract_status="BLOCKED_COMMAND_CONTRACT_MISSING",
            code="COMMAND_CONTRACT_MISSING",
            message=f"command contract missing: {rel}",
            result_text=(
                "STATUS: BLOCKED_COMMAND_CONTRACT_MISSING\n"
                f"COMMAND: /{command_id}\n"
                "MODE: READ_ONLY\n"
                f"NEXT_ACTION: restore {rel} and rerun\n"
            ),
            contract_path=rel,
            contract_sha256="MISSING",
        )
    rel, digest = identity
    result_text = (
        f"STATUS: {status}\n"
        f"COMMAND: /{command_id}\n"
        "MODE: READ_ONLY\n"
        f"COMMAND_CONTRACT_SHA256: {digest}\n"
        f"WHY: {why}\n"
        f"NEXT_ACTION: {next_action}\n"
    )
    return CommandPreflightBlock(
        command_id=command_id,
        contract_status=status,
        code=code,
        message=f"{status}: {why}; contract_sha256={digest}",
        result_text=result_text,
        contract_path=rel,
        contract_sha256=digest,
    )


def validate_command_invocation(
    request: CursorExecutionRequest,
) -> CommandPreflightBlock | None:
    """Return a fail-closed terminal block only for deterministic invalid inputs."""

    command_id, arguments = _slash_command(request.prompt)
    if command_id is None:
        return None

    upper_tokens = {part.upper() for part in arguments.split()}
    values = _tokens(arguments)

    if command_id == "opsx-doctor" and "GO" in upper_tokens:
        return _block(
            request,
            command_id=command_id,
            status="BLOCKED",
            code="COMMAND_GO_UNSUPPORTED",
            why="destructive GO is unsupported by the target doctor contract",
            next_action="/opsx-doctor CHECK",
        )

    if command_id == "opsx-meta-update-all-snapshots" and "GO" in upper_tokens:
        return _block(
            request,
            command_id=command_id,
            status="FAIL_CLOSED",
            code="SNAPSHOT_CENTRAL_OVERRIDE_MISSING",
            why="target catalog helper lacks --central and foreign snapshot apply is forbidden",
            next_action="/opsx-meta-fix-command-defects",
        )

    if command_id == "opsx-models":
        scope = values.get("scope")
        if scope is not None and scope not in {"effective", "project", "runtime"}:
            return _block(
                request,
                command_id=command_id,
                status="OWNER_ATTENTION",
                code="COMMAND_SCOPE_INVALID",
                why=f"unsupported model configuration scope: {scope}",
                next_action="use scope=effective|project|runtime",
            )

    if command_id == "opsx-plan" and arguments.strip() == "GO":
        return _block(
            request,
            command_id=command_id,
            status="BLOCKED_NEEDS_INTENT",
            code="COMMAND_INPUT_REQUIRED",
            why="GO is not write authorization and no change or feature intent was supplied",
            next_action="provide change=<id> or feature intent",
        )

    if command_id == "opsx-rename-change" and "GO" in upper_tokens and not values.get("change"):
        return _block(
            request,
            command_id=command_id,
            status="BLOCKED_NEEDS_CHANGE",
            code="COMMAND_INPUT_REQUIRED",
            why="rename GO requires an explicit active change binding",
            next_action="provide change=<old-kebab> and a fresh choice",
        )

    if command_id == "opsx-meta-analyze-pipeline":
        stripped = arguments.strip()
        if stripped in {"EXAMPLE_CHAT:", "CHAT_ID:"}:
            return _block(
                request,
                command_id=command_id,
                status="BLOCKED",
                code="EXAMPLE_CHAT_UNREADABLE",
                why="explicit transcript selector is empty and cannot resolve bounded evidence",
                next_action="provide EXAMPLE_CHAT:<id> or omit the selector for normal read-only analysis",
            )

    empty_rule = _EMPTY_ARGUMENT_RULES.get(command_id)
    if empty_rule is not None and not arguments:
        status, code, why, next_action = empty_rule
        return _block(
            request,
            command_id=command_id,
            status=status,
            code=code,
            why=why,
            next_action=next_action,
        )

    if command_id not in {"opsx-bug-disposition", "opsx-bug-family-plan"}:
        return None

    if command_id == "opsx-bug-disposition":
        if "GO" in upper_tokens:
            return _block(
                request,
                command_id=command_id,
                status="GO_UNSUPPORTED",
                code="COMMAND_GO_UNSUPPORTED",
                why="GO is not authorized for this read-only routing command",
                next_action="rerun without GO and provide change=<id> case=<id> finding=<id>",
            )
        missing = [key for key in ("change", "case", "finding") if not values.get(key, "").strip()]
        if missing:
            return _block(
                request,
                command_id=command_id,
                status="BLOCKED_NEEDS_INPUT",
                code="COMMAND_INPUT_REQUIRED",
                why="missing required explicit tokens: " + ",".join(missing),
                next_action="provide change=<id> case=<id> finding=<id>",
            )
        return None

    has_clean_block = "CLEAN_FINDINGS" in request.prompt
    has_single_explicit_finding = bool(values.get("finding", "").strip())
    if "GO" in upper_tokens and not (has_clean_block or has_single_explicit_finding):
        return _block(
            request,
            command_id=command_id,
            status="GO_UNSUPPORTED",
            code="COMMAND_GO_UNSUPPORTED",
            why="GO cannot manufacture a missing clean finding set",
            next_action="provide CLEAN_FINDINGS or one grounded finding and rerun without relying on GO",
        )
    if not has_clean_block and not has_single_explicit_finding:
        return _block(
            request,
            command_id=command_id,
            status="BLOCKED_NEEDS_INPUT",
            code="COMMAND_INPUT_REQUIRED",
            why="no CLEAN_FINDINGS block or explicit grounded finding was supplied",
            next_action="provide CLEAN_FINDINGS or one grounded finding",
        )
    return None
