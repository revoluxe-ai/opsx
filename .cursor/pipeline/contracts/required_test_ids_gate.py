"""Shared GATE_INTEGRITY: required test ids supplied by scenario audit (not plan parse)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RequiredTestSkipVerdict:
    scenario_id: str
    required_test_ids: list[str]
    row_verdict: str
    gap_class: str | None
    disabled_test_id: str | None = None


def test_id_is_skipped(repo_root: Path, node_id: str) -> str | None:
    """Return skip reason if node id is skipped in source; None if enabled."""

    if "::" not in node_id:
        return None
    rel, func_part = node_id.split("::", 1)
    func = func_part.split("[", 1)[0]
    path = repo_root / rel
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    if f"def {func}(" not in text and f"async def {func}(" not in text:
        return None
    wf = re.search(
        rf"@pytest\.mark\.skip\([^)]+\)\s*\n(?:async\s+def|def)\s+{re.escape(func)}\s*\(",
        text,
        re.MULTILINE,
    )
    if wf:
        sm = re.search(r"reason\s*=\s*[\"']([^\"']*)[\"']", wf.group(0))
        return sm.group(1) if sm else "skip"
    if "[" in func_part:
        param_id = func_part.split("[", 1)[1].rstrip("]")
        if "pytest.param(" in text and "pytest.mark.skip" in text:
            if f'id="{param_id}"' in text or f"id='{param_id}'" in text:
                block = re.search(
                    rf"pytest\.param\([^)]*id\s*=\s*[\"']{re.escape(param_id)}[\"'][^)]*pytest\.mark\.skip",
                    text,
                    re.DOTALL,
                )
                if block:
                    return "param_skip"
            if f"pytest.param({param_id}" in text and "pytest.mark.skip" in text:
                return "param_skip"
            if f'pytest.param("{param_id}"' in text and "pytest.mark.skip" in text:
                return "param_skip"
    return None


def audit_required_test_ids(
    repo_root: Path,
    scenario_id: str,
    required_test_ids: list[str],
) -> RequiredTestSkipVerdict:
    """Authority for /opsx-spec-scenario-coverage GATE_INTEGRITY (inspect-only).

    Caller passes concrete mapped-required pytest node ids from the scenario audit
    row (SCENARIO_EVIDENCE_ROW). This gate does not parse scenario plan markdown.
    """

    for tid in required_test_ids:
        reason = test_id_is_skipped(repo_root, tid)
        if reason is not None:
            return RequiredTestSkipVerdict(
                scenario_id=scenario_id,
                required_test_ids=list(required_test_ids),
                row_verdict="FAIL",
                gap_class="DISABLED_PROOF_SKIP",
                disabled_test_id=tid,
            )
    return RequiredTestSkipVerdict(
        scenario_id=scenario_id,
        required_test_ids=list(required_test_ids),
        row_verdict="PASS",
        gap_class=None,
    )


def audit_required_test_ids_row(
    repo_root: Path,
    *,
    plan_md_path: Path,
    scenario_id: str,
    required_test_ids: list[str] | None = None,
) -> RequiredTestSkipVerdict:
    """Backward-compatible wrapper; ``required_test_ids`` must be supplied explicitly."""

    if required_test_ids is None:
        return RequiredTestSkipVerdict(
            scenario_id=scenario_id,
            required_test_ids=[],
            row_verdict="PASS",
            gap_class=None,
        )
    return audit_required_test_ids(repo_root, scenario_id, required_test_ids)
