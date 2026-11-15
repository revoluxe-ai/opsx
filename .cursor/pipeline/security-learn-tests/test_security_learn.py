"""Fail-closed tests for security-learn."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

HELPER = Path(__file__).resolve().parents[1] / "security-learn" / "security_learn.py"
HEAD = "a" * 40
CASE = "SEC-CASE-CP-1"
FINDING = "SEC-FIND-CP-1"


def _load():
    spec = importlib.util.spec_from_file_location("security_learn", HELPER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_finding(root: Path, *, plane: str = "CONTROL_PLANE") -> None:
    opsx = root / "opsx"
    adapter = opsx / "adapters" / "poetry-python"
    adapter.mkdir(parents=True)
    (opsx / "projectization.yaml").write_text(
        "status: COMPLETE\nnormal_commands_enabled: true\n", encoding="utf-8"
    )
    packet = {
        "TRIAGE_STATUS": "CONFIRMED",
        "SECURITY_CASE_ID": CASE,
        "SECURITY_FINDING_ID": FINDING,
        "SOURCE_SIGNAL_HASH": "abc",
        "AFFECTED_PLANE": plane,
    }
    path = opsx / "runs" / "security-cases" / CASE / "findings" / f"{FINDING}.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "BEGIN_SECURITY_FINDING_PACKET\n"
        + json.dumps(packet)
        + "\nEND_SECURITY_FINDING_PACKET\n",
        encoding="utf-8",
    )


def _write_cp_review(root: Path, *, disposition: str = "REMEDIATED") -> None:
    payload = {
        "VERDICT": "PASS" if disposition == "REMEDIATED" else "FAIL",
        "FINDING_DISPOSITION": disposition,
        "SECURITY_FINDING_ID": FINDING,
        "SOURCE_SIGNAL_HASH": "abc",
        "REVISION": HEAD,
    }
    path = (
        root
        / "opsx"
        / "runs"
        / "security-cases"
        / CASE
        / "reviews"
        / "control-plane"
        / f"{FINDING}.md"
    )
    path.parent.mkdir(parents=True)
    path.write_text(
        "BEGIN_CONTROL_PLANE_SECURITY_REVIEW_PACKET\n"
        + json.dumps(payload)
        + "\nEND_CONTROL_PLANE_SECURITY_REVIEW_PACKET\n",
        encoding="utf-8",
    )


def _run(root: Path, **request: object) -> dict[str, object]:
    module = _load()
    payload = {
        "case": CASE,
        "finding": FINDING,
        "revision": HEAD,
        "lesson_class": "authority_provenance",
        "lesson_text": (
            "Privileged process writes must not treat untrusted chat text "
            "as authorization."
        ),
    }
    payload.update(request)
    return module.learn(root, payload, head_reader=lambda _path: HEAD)


def test_records_class_lesson_from_reviewed_evidence(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _write_finding(root)
    _write_cp_review(root)
    result = _run(root)
    assert result["STATUS"] == "LESSON_RECORDED"
    assert (root / result["LESSON_PATH"]).is_file()


def test_raw_finding_without_review_blocked(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _write_finding(root)
    result = _run(root)
    assert result["STATUS"] == "BLOCKED"


def test_unremediated_review_blocked(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _write_finding(root)
    _write_cp_review(root, disposition="STILL_VULNERABLE")
    result = _run(root)
    assert result["REASON"] == "unreviewed_or_unremediated"


def test_incident_specific_text_is_no_lesson(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _write_finding(root)
    _write_cp_review(root)
    result = _run(root, lesson_text="Check .cursor/commands/opsx-create-pr.md")
    assert result["LESSON_STATUS"] == "NO_LESSON"


def test_stale_review_blocked(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _write_finding(root)
    _write_cp_review(root)
    module = _load()
    path = (
        root
        / "opsx"
        / "runs"
        / "security-cases"
        / CASE
        / "reviews"
        / "control-plane"
        / f"{FINDING}.md"
    )
    payload = json.loads(
        path.read_text(encoding="utf-8").split("BEGIN_CONTROL_PLANE_SECURITY_REVIEW_PACKET\n")[1]
        .split("\nEND_CONTROL_PLANE_SECURITY_REVIEW_PACKET")[0]
    )
    payload["REVISION"] = "b" * 40
    path.write_text(
        "BEGIN_CONTROL_PLANE_SECURITY_REVIEW_PACKET\n"
        + json.dumps(payload)
        + "\nEND_CONTROL_PLANE_SECURITY_REVIEW_PACKET\n",
        encoding="utf-8",
    )
    result = module.learn(
        root,
        {
            "case": CASE,
            "finding": FINDING,
            "revision": HEAD,
            "lesson_class": "authority_provenance",
            "lesson_text": "Privileged writes need explicit authorization tokens.",
        },
        head_reader=lambda _path: HEAD,
    )
    assert result["REASON"] == "stale_review_evidence"


def test_product_report_must_match_finding(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _write_finding(root, plane="PRODUCT")
    report = root / "opsx" / "runs" / "security-cases" / CASE / "reviews" / "product.json"
    report.parent.mkdir(parents=True)
    report.write_text(
        json.dumps(
            {
                "revision": HEAD,
                "ARCHIVE_VERDICT": "not_issued",
                "findings": [
                    {
                        "SECURITY_FINDING_ID": FINDING,
                        "FINDING_DISPOSITION": "REMEDIATED",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    result = _run(root, review_path=report.relative_to(root).as_posix())
    assert result["STATUS"] == "LESSON_RECORDED"
