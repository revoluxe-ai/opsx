# -*- coding: utf-8 -*-
"""Regression: authority boundaries for verification commands (no silent expansion)."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
COMMANDS = REPO_ROOT / ".cursor" / "commands"


def _read(name: str) -> str:
    return (COMMANDS / name).read_text(encoding="utf-8")


def test_read_only_coverage_command_no_git_writes() -> None:
    body = _read("opsx-static-check-coverage.md")
    alias = _read("opsx-pyright-strict-coverage.md")
    assert "canonical" in alias.lower() or "opsx-static-check-coverage" in alias
    forbidden = ("git commit", "git push", "poetry publish")
    lowered = body.lower()
    for token in forbidden:
        assert token not in lowered, f"forbidden {token} in coverage command"


def test_plan_verification_freeze_does_not_draft() -> None:
    body = _read("opsx-plan-verification.md")
    assert "freeze" in body.lower()
    assert "freeze_plan" in body or "bounded_verify_plan.freeze_plan" in body
    assert "DRAFT plan already published" in body
    assert "sole" in body.lower() or "interface only" in body.lower()


def test_compose_does_not_substitute_static_fast_as_full_required() -> None:
    body = _read("opsx-compose-verification-plan.md")
    assert "full_required" in body
    assert "PLAN_COVERAGE_INCOMPLETE" in body
    assert "static_fast" in body
    lowered = body.lower()
    assert "substitute" in lowered or "not full" in lowered or "fail_closed" in lowered


def test_apply_single_canonical_spine() -> None:
    body = _read("opsx-apply.md")
    assert "/opsx-compose-verification-plan" in body
    assert body.count("Then: `/opsx-pyright-strict-coverage") == 0 or "legacy" in body.lower()


def main() -> int:
    test_read_only_coverage_command_no_git_writes()
    test_plan_verification_freeze_does_not_draft()
    test_compose_does_not_substitute_static_fast_as_full_required()
    test_apply_single_canonical_spine()
    print("authority boundary tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
