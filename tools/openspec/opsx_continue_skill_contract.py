"""Контракт pre-write для skill ``openspec-continue-change`` ≡ ``/opsx-continue``.

Проверяет выравнивание SKILL.md с командой, gate projectization, обязательный
``openspec_guard.py`` и TARGET-local CLI provider. Не дублирует измерения guard —
делегирует сценарий ``opsx_continue`` существующему скрипту.
"""

from __future__ import annotations

from opsx.project_context import resource_path

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from tools.openspec.openspec_cli_provider import validate_openspec_cli_provider
from tools.openspec.opsx_mutation_authority import enforce_mutation_authority

CONTRACT_IMPLEMENTATION_ID = "stroy-opsx-continue-skill-contract-v0.1.0"

GUARD_REL = Path("tools/openspec/openspec_guard.py")
SKILL_REL = Path("opsx/core/.cursor/skills/openspec-continue-change/SKILL.md")
COMMAND_REL = Path(".cursor/commands/opsx-continue.md")
PROJECTIZATION_REL = Path("opsx/projectization.yaml")
TRANSFER_STATE_REL = Path("opsx/transfer/TRANSFER_STATE.json")

# Каноническая однострочная команда из SKILL.md (repo root, без PYTHONPATH).
SKILL_PREFLIGHT_INVOCATION = (
    "python -m tools.openspec.opsx_continue_skill_contract --preflight-before-write"
)

REQUIRED_SKILL_MARKERS: tuple[str, ...] = (
    "OPSX_PROJECTIZATION_REQUIRED",
    "OPSX_ADOPTION_REQUIRED",
    SKILL_PREFLIGHT_INVOCATION,
    "python tools/openspec/openspec_guard.py --scenario opsx_continue --strict --change",
    "--guard-stage early",
    "opsx_tools/openspec-cli",
    "opsx-source-resolution.md",
    "opsx-execution-preflight.md",
    "OPENSPEC_CLI_FAIL",
    "no global `openspec`",
    "no `npx`",
    "STROY-SYSTEM TARGET OVERRIDE",
    "capabilities.yaml",
)

# Authority parity with ``opsx-continue.md`` (spec lifecycle + Derived Lifecycle).
REQUIRED_AUTHORITY_MARKERS: tuple[str, ...] = (
    "MUST cover every materially observable lifecycle transition",
    "MUST NOT invent behavior absent from lifecycle",
    "Do **not** invent semantic transitions",
    "UNRESOLVED_TRANSITION",
    "SOURCE_GAP",
    "MUST NOT add product semantics",
    "do **not** invent a ceremonial lifecycle",
    "CONFIRM_LIFECYCLE",
)

FORBIDDEN_SKILL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?<!\.)openspec\s+list\b"),
    re.compile(r"(?<!\.)openspec\s+status\b"),
    re.compile(r"(?<!\.)openspec\s+instructions\b"),
    re.compile(r"(?<!\.)openspec\s+store\s+list\b"),
    re.compile(r"\bnpx\s+"),
    re.compile(r"Bash\(openspec:\*\)"),
)


@dataclass(frozen=True)
class ContinuePreflightResult:
    """Итог одной проверки pre-write (fail-closed)."""

    ok: bool
    reason: str
    detail: str = ""


def read_projectization(root: Path) -> dict[str, object]:
    """Прочитать ``opsx/projectization.yaml`` или вернуть пустой dict."""

    path = resource_path(root, PROJECTIZATION_REL)
    if not path.is_file():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def projectization_allows_commands(root: Path) -> ContinuePreflightResult:
    """Gate ``OPSX_PROJECTIZATION_REQUIRED`` (только ``projectization.yaml``)."""

    data = read_projectization(root)
    if not data:
        return ContinuePreflightResult(
            ok=False,
            reason="OPSX_PROJECTIZATION_REQUIRED",
            detail="missing projectization.yaml",
        )
    status = str(data.get("status", ""))
    normal = data.get("normal_commands_enabled")
    if status != "COMPLETE" or normal is not True:
        return ContinuePreflightResult(
            ok=False,
            reason="OPSX_PROJECTIZATION_REQUIRED",
            detail=f"status={status!r} normal_commands_enabled={normal!r}",
        )
    return ContinuePreflightResult(ok=True, reason="OK")


def stale_transfer_complete_but_lock_blocked(root: Path) -> bool:
    """True, если transfer JSON говорит COMPLETE, а lock — нет (stale authority)."""

    lock = read_projectization(root)
    lock_status = str(lock.get("status", ""))
    transfer_path = resource_path(root, TRANSFER_STATE_REL)
    if not transfer_path.is_file():
        return False
    try:
        transfer = json.loads(transfer_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    transfer_status = str(transfer.get("projectization_status", ""))
    return transfer_status == "COMPLETE" and lock_status != "COMPLETE"


def check_command_skill_authority_parity(root: Path) -> list[str]:
    """Проверить spec-lifecycle и Derived Lifecycle authority в SKILL vs command."""

    skill_path = resource_path(root, SKILL_REL)
    command_path = resource_path(root, COMMAND_REL)
    violations: list[str] = []
    if not command_path.is_file():
        violations.append(f"missing {COMMAND_REL}")
        return violations
    if not skill_path.is_file():
        violations.append(f"missing {SKILL_REL}")
        return violations
    def _norm(text: str) -> str:
        return re.sub(r"\s+", " ", text)

    skill_text = _norm(skill_path.read_text(encoding="utf-8"))
    command_text = _norm(command_path.read_text(encoding="utf-8"))
    for marker in REQUIRED_AUTHORITY_MARKERS:
        needle = _norm(marker)
        if needle not in skill_text:
            violations.append(f"missing authority marker in skill: {marker}")
    for marker in REQUIRED_AUTHORITY_MARKERS:
        needle = _norm(marker)
        if needle not in command_text:
            violations.append(f"missing authority marker in command: {marker}")
    return violations


def check_skill_prose_alignment(root: Path) -> list[str]:
    """Список нарушений выравнивания SKILL.md (пустой — OK)."""

    skill_path = resource_path(root, SKILL_REL)
    if not skill_path.is_file():
        return [f"missing {SKILL_REL}"]
    text = skill_path.read_text(encoding="utf-8")
    violations: list[str] = []
    for marker in REQUIRED_SKILL_MARKERS:
        if marker not in text:
            violations.append(f"missing required marker: {marker}")
    for pattern in FORBIDDEN_SKILL_PATTERNS:
        if pattern.search(text):
            violations.append(f"forbidden pattern: {pattern.pattern}")
    violations.extend(check_command_skill_authority_parity(root))
    return violations


def check_skill_contract_for_guard(root: Path) -> list[str]:
    """Контракт для ``openspec_guard`` scenario ``opsx_continue`` (без projectization)."""

    return check_skill_prose_alignment(root)


def run_guard_strict(root: Path) -> ContinuePreflightResult:
    """Запустить ``openspec_guard.py --scenario opsx_continue --strict``."""

    guard = resource_path(root, GUARD_REL)
    if not guard.is_file():
        return ContinuePreflightResult(
            ok=False,
            reason="GUARD_MISSING",
            detail=str(GUARD_REL),
        )
    proc = subprocess.run(
        [
            sys.executable,
            str(guard),
            "--scenario",
            "opsx_continue",
            "--strict",
            "--root",
            str(root),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return ContinuePreflightResult(
            ok=False,
            reason="GUARD_FAILED",
            detail=(proc.stderr or proc.stdout or "").strip()[:2000],
        )
    return ContinuePreflightResult(ok=True, reason="OK")


def check_cli_provider_gate(root: Path) -> ContinuePreflightResult:
    """Проверить TARGET-local CLI provider (без npx/global)."""

    results = validate_openspec_cli_provider(root)
    if any(item.ok and item.code == "OK" for item in results):
        return ContinuePreflightResult(ok=True, reason="OK")
    codes = ", ".join(f"{item.code}" for item in results if not item.ok)
    return ContinuePreflightResult(
        ok=False,
        reason="OPENSPEC_CLI_FAIL",
        detail=codes or "provider validation failed",
    )


def evaluate_planning_write_preflight(root: Path) -> ContinuePreflightResult:
    """Полный fail-closed pre-write для continue (projectization → guard → CLI)."""

    alignment = check_skill_prose_alignment(root)
    if alignment:
        return ContinuePreflightResult(
            ok=False,
            reason="SKILL_ALIGNMENT_FAIL",
            detail="; ".join(alignment[:5]),
        )

    mutation = enforce_mutation_authority(root, "python.skill.opsx_continue")
    if not mutation.ok:
        return ContinuePreflightResult(
            ok=False,
            reason=mutation.reason,
            detail=mutation.detail,
        )

    guard = run_guard_strict(root)
    if not guard.ok:
        return guard

    cli = check_cli_provider_gate(root)
    if not cli.ok:
        return cli

    return ContinuePreflightResult(ok=True, reason="OK")


def command_surface_exists(root: Path) -> bool:
    """Проверить наличие канонической command surface."""

    return (resource_path(root, COMMAND_REL)).is_file()


def build_parser() -> argparse.ArgumentParser:
    """CLI для bound pre-write path skill ``openspec-continue-change``."""

    parser = argparse.ArgumentParser(
        description="Fail-closed pre-write contract for openspec-continue-change.",
    )
    parser.add_argument(
        "--preflight-before-write",
        action="store_true",
        help="Run full continue skill preflight (alignment, projectization, guard, CLI).",
    )
    parser.add_argument(
        "--guard-skill-contract",
        action="store_true",
        help="Guard hook: skill/command contract only (invoked by openspec_guard.py).",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Repository root (default: current working directory).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry: ``--preflight-before-write`` для skill execution path."""

    args = build_parser().parse_args(argv)
    root = (args.root or Path.cwd()).resolve()
    if args.guard_skill_contract:
        violations = check_skill_contract_for_guard(root)
        if violations:
            for item in violations:
                print(f"FAIL SKILL_CONTRACT: {item}", file=sys.stderr)
            return 1
        return 0
    if not args.preflight_before_write:
        print(
            "FAIL USAGE: pass --preflight-before-write or --guard-skill-contract",
            file=sys.stderr,
        )
        return 1
    result = evaluate_planning_write_preflight(root)
    if not result.ok:
        print(
            f"FAIL {result.reason}: {result.detail}",
            file=sys.stderr,
        )
        return 1
    print(
        f"PASS continue-skill-preflight implementation={CONTRACT_IMPLEMENTATION_ID}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
