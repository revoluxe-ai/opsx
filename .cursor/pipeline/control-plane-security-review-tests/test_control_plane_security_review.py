"""Fail-closed tests for control-plane security review."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

HELPER = (
    Path(__file__).resolve().parents[1]
    / "control-plane-security-review"
    / "control_plane_security_review.py"
)
HEAD = "a" * 40
CASE = "SEC-CASE-CP-1"
FINDING = "SEC-FIND-CP-1"
QUOTE = "plaintext GO is accepted"


def _load():
    spec = importlib.util.spec_from_file_location(
        "control_plane_security_review", HELPER
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _init(root: Path, *, plane: str = "CONTROL_PLANE", source: str = QUOTE) -> None:
    opsx = root / "opsx"
    adapter = opsx / "adapters" / "poetry-python"
    adapter.mkdir(parents=True)
    (opsx / "projectization.yaml").write_text(
        "status: COMPLETE\nnormal_commands_enabled: true\n", encoding="utf-8"
    )
    cited = root / ".cursor" / "commands" / "opsx-create-pr.md"
    cited.parent.mkdir(parents=True)
    cited.write_text(source + "\n", encoding="utf-8")
    packet = {
        "TRIAGE_STATUS": "CONFIRMED",
        "SECURITY_CASE_ID": CASE,
        "SECURITY_FINDING_ID": FINDING,
        "SOURCE_SIGNAL_HASH": "abc",
        "AFFECTED_PLANE": plane,
        "PATH": ".cursor/commands/opsx-create-pr.md",
        "EVIDENCE": [
            {
                "PATH": ".cursor/commands/opsx-create-pr.md",
                "QUOTE": QUOTE,
                "LINE": 1,
            }
        ],
    }
    finding = (
        opsx / "runs" / "security-cases" / CASE / "findings" / f"{FINDING}.md"
    )
    finding.parent.mkdir(parents=True)
    finding.write_text(
        "# SECURITY_FINDING_PACKET\n\nBEGIN_SECURITY_FINDING_PACKET\n"
        + json.dumps(packet, indent=2)
        + "\nEND_SECURITY_FINDING_PACKET\n",
        encoding="utf-8",
    )


def _run(root: Path, **request: object) -> dict[str, object]:
    module = _load()
    payload = {"case": CASE, "finding": FINDING, "revision": HEAD}
    payload.update(request)
    return module.review(root, payload, head_reader=lambda _path: HEAD)


def test_pre_remediation_fail(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _init(root)
    result = _run(root)
    assert result["STATUS"] == "FAIL"
    assert result["FINDING_DISPOSITION"] == "STILL_VULNERABLE"
    assert (root / result["REVIEW_PATH"]).is_file()


def test_independent_pass(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _init(root, source="AUTHORIZE_COMMAND_FIX is required\n")
    rem = (
        root
        / "opsx"
        / "runs"
        / "security-cases"
        / CASE
        / "remediation"
        / "control-plane"
        / "CONTROL-PLANE-REMEDIATION-1.md"
    )
    rem.parent.mkdir(parents=True)
    rem.write_text(f"fixed {FINDING}\n", encoding="utf-8")
    result = _run(
        root,
        remediating_quote="AUTHORIZE_COMMAND_FIX is required",
        reviewer_id="reviewer-a",
        remediator_id="fixer-b",
        remediation_path=rem.relative_to(root).as_posix(),
    )
    assert result["STATUS"] == "PASS"
    assert result["FINDING_DISPOSITION"] == "REMEDIATED"


def test_self_certification_forbidden(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _init(root, source="AUTHORIZE_COMMAND_FIX is required\n")
    result = _run(
        root,
        remediating_quote="AUTHORIZE_COMMAND_FIX is required",
        reviewer_id="same",
        remediator_id="same",
    )
    assert result["REASON"] == "self_certification_forbidden"


def test_product_plane_not_applicable(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _init(root, plane="PRODUCT")
    result = _run(root)
    assert result["STATUS"] == "NOT_APPLICABLE"


def test_stale_revision_blocked(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _init(root)
    result = _run(root, revision="b" * 40)
    assert result["REASON"] == "stale_revision"


def test_path_escape_blocked(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _init(root)
    result = _run(root, finding_path="../secrets.md")
    assert result["REASON"] == "path_outside_workspace"
