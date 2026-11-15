"""Central OPSX mutation authority: registry, enforcement, CLI."""

from __future__ import annotations

from opsx.project_context import resource_path

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.openspec.opsx_engineering_adoption_gate import (
    AdoptionGateResult,
    BOOTSTRAP_COMMAND_ALLOWLIST,
)
from tools.openspec.opsx_mutation_preflight import evaluate_opsx_mutation_preflight

AUTHORITY_IMPLEMENTATION_ID = "stroy-opsx-mutation-authority-v1.0.0"
MATRIX_REL = Path("opsx/registry/mutation_entrypoint_matrix.json")

# Entrypoints that MUST call enforce_mutation_authority before protected writes.
REGISTERED_ENTRYPOINTS: frozenset[str] = frozenset(
    {
        "python.skill.opsx_continue",
        "python.guard.opsx_continue",
        "python.guard.opsx_ff",
        "python.guard.opsx_update",
        "python.guard.opsx_sync",
        "python.guard.opsx_apply",
        "node.apply_change_writer.acquire",
        "node.apply_change_writer.mutation_guard",
        "bash.openspec_cli.write",
        "python.pipeline.browser_check",
        "python.pipeline.post_deploy_acceptance",
        "python.pipeline.security_learn",
        "python.pipeline.security_review",
        "python.pipeline.security_triage",
        "python.pipeline.control_plane_security_review",
        "python.pipeline.orch_bug_fix",
        "python.pipeline.orch_bugbot_converge",
    }
)

ENTRYPOINT_TO_COMMAND_ID: dict[str, str] = {
    "python.skill.opsx_continue": "opsx-continue",
    "python.guard.opsx_continue": "opsx-continue",
    "python.guard.opsx_ff": "opsx-ff",
    "python.guard.opsx_update": "opsx-update",
    "python.guard.opsx_sync": "opsx-sync",
    "python.guard.opsx_apply": "opsx-apply",
    "bash.openspec_cli.write": "opsx-continue",
}

GUARD_MUTATION_SCENARIOS: frozenset[str] = frozenset(
    {"opsx_continue", "opsx_ff", "opsx_update", "opsx_sync", "opsx_apply"}
)


@dataclass(frozen=True)
class AuthorityResult:
    """Итог enforce для runtime hook."""

    ok: bool
    reason: str
    detail: str = ""
    next_action: str = "/opsx-adopt"
    entrypoint_id: str = ""

    def as_blocked_dict(self) -> dict[str, str]:
        return {
            "STATUS": "FAIL_CLOSED",
            "REASON": self.reason,
            "NEXT_ACTION": self.next_action,
            "DETAIL": self.detail,
            "ENTRYPOINT_ID": self.entrypoint_id,
        }


def load_entrypoint_matrix(root: Path) -> dict[str, Any]:
    path = resource_path(root, MATRIX_REL)
    if not path.is_file():
        return {"schema": "opsx.mutation_entrypoint_matrix", "entrypoints": []}
    return json.loads(path.read_text(encoding="utf-8"))


def enforce_mutation_authority(root: Path, entrypoint_id: str) -> AuthorityResult:
    """Fail-closed unless bootstrap allowlist or verified adoption."""

    root = root.resolve()
    if entrypoint_id not in REGISTERED_ENTRYPOINTS:
        return AuthorityResult(
            ok=False,
            reason="MUTATION_ENTRYPOINT_UNREGISTERED",
            detail=f"entrypoint_id={entrypoint_id!r}",
            entrypoint_id=entrypoint_id,
        )

    command_id = ENTRYPOINT_TO_COMMAND_ID.get(entrypoint_id)
    if command_id and command_id in BOOTSTRAP_COMMAND_ALLOWLIST:
        return AuthorityResult(ok=True, reason="BOOTSTRAP_ALLOWLIST", entrypoint_id=entrypoint_id)

    preflight = evaluate_opsx_mutation_preflight(root, command_id=command_id)
    if not preflight.ok:
        return AuthorityResult(
            ok=False,
            reason=preflight.reason,
            detail=preflight.detail,
            next_action=preflight.next_action if preflight.reason == "OPSX_ADOPTION_REQUIRED" else "/opsx-adopt",
            entrypoint_id=entrypoint_id,
        )
    return AuthorityResult(ok=True, reason="OK", entrypoint_id=entrypoint_id)


def enforce_guard_scenario(root: Path, scenario_id: str) -> AuthorityResult | None:
    """Guard scenarios that imply artifact writes."""

    if scenario_id not in GUARD_MUTATION_SCENARIOS:
        return None
    entrypoint = f"python.guard.{scenario_id}"
    return enforce_mutation_authority(root, entrypoint)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OPSX central mutation authority.")
    parser.add_argument("--enforce", action="store_true")
    parser.add_argument("--entrypoint-id", required=False)
    parser.add_argument("--root", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = (args.root or Path.cwd()).resolve()
    if not args.enforce or not args.entrypoint_id:
        print("FAIL USAGE: --enforce --entrypoint-id <id> [--root PATH]", file=sys.stderr)
        return 2
    result = enforce_mutation_authority(root, args.entrypoint_id)
    if result.ok:
        print(json.dumps({"STATUS": "OK", "implementation": AUTHORITY_IMPLEMENTATION_ID}))
        return 0
    print(json.dumps(result.as_blocked_dict()))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
