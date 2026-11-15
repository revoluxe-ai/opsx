"""Negative fixtures for the read-only PRODUCT security delta report."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


HELPER = (
    Path(__file__).resolve().parents[1] / "security-review" / "security_review.py"
)
REPO = Path(__file__).resolve().parents[3]
HEAD_A = "a" * 40
HEAD_B = "b" * 40
CHANGE = "sec-delta-fixture"
QUOTE = '"localhost" in origin'
FIX_QUOTE = "parsed_host == allowed_host"
REGISTRY_WITHOUT = "checks:\n  pyright-strict:\n    task_type: shell\n"
REGISTRY_WITH = "checks:\n  security_review:\n    task_type: shell\n"
FORBIDDEN = {"PASS", "PASS_WITH_RISK", "NOT_APPLICABLE"}


def _load():
    spec = importlib.util.spec_from_file_location("security_review", HELPER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load security_review")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _init(root: Path) -> None:
    opsx = root / "opsx"
    adapter = opsx / "adapters" / "poetry-python"
    adapter.mkdir(parents=True)
    _ = (opsx / "projectization.yaml").write_text(
        "status: COMPLETE\nnormal_commands_enabled: true\n",
        encoding="utf-8",
    )
    _ = (adapter / "README.md").write_text("# poetry-python\n", encoding="utf-8")
    change = root / "openspec" / "changes" / CHANGE
    change.mkdir(parents=True)
    _ = (change / "proposal.md").write_text("product delta\n", encoding="utf-8")


def _snapshot(root: Path) -> list[str]:
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    )


def _write_source(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(text, encoding="utf-8")


def _write_packet(
    root: Path,
    *,
    case: str,
    finding: str,
    packet: dict[str, object],
) -> None:
    path = (
        root
        / "opsx"
        / "runs"
        / "security-cases"
        / case
        / "findings"
        / f"{finding}.md"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(packet, ensure_ascii=True, indent=2, sort_keys=True)
    _ = path.write_text(
        "# SECURITY_FINDING_PACKET\n\n"
        "BEGIN_SECURITY_FINDING_PACKET\n"
        f"{payload}\n"
        "END_SECURITY_FINDING_PACKET\n",
        encoding="utf-8",
    )


def _packet(
    *,
    case: str,
    finding: str,
    signal_hash: str,
    path: str,
    quote: str = QUOTE,
    plane: str = "PRODUCT",
    status: str = "BOUND",
    change: str = CHANGE,
    observed: str = "",
) -> dict[str, object]:
    packet: dict[str, object] = {
        "TRIAGE_STATUS": "CONFIRMED",
        "SECURITY_CASE_ID": case,
        "SECURITY_FINDING_ID": finding,
        "SOURCE_SIGNAL_HASH": signal_hash,
        "AFFECTED_PLANE": plane,
        "PATH": path,
        "EVIDENCE": [{"PATH": path, "QUOTE": quote, "LINE": 1}],
        "CHANGE_BINDING": {"CHANGE_ID": change, "STATUS": status},
    }
    if observed:
        packet["OBSERVED_REVISION"] = observed
    return packet


def _review(root: Path, **overrides: object) -> dict[str, object]:
    module = _load()
    request: dict[str, object] = {
        "mode": "report",
        "change": CHANGE,
        "revision": HEAD_A,
    }
    request.update(overrides)
    result = module.review(
        root,
        request,
        head_reader=lambda _path: HEAD_A,
        registry_text=REGISTRY_WITHOUT,
    )
    if not isinstance(result, dict):
        raise AssertionError("review did not return an object")
    return result


def _assert_no_archive_pass(result: dict[str, object]) -> None:
    assert result["STATUS"] not in FORBIDDEN
    assert result["ARCHIVE_VERDICT"] == "not_issued"
    assert result["PRODUCT_SECURITY_VERDICT"] == "not_issued"
    assert result["DURABLE_RECEIPT"] == "NONE"
    assert result["SECURITY_REVIEW_PACKET"] == "absent"
    assert result["SOURCE_PARITY"] == "PARTIAL"
    assert result["FORBIDDEN_ARCHIVE_STATUS_EMITTED"] == "NO"


def test_revision_must_match_current_head(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    _init(root)
    mismatch = _review(root, revision=HEAD_B)
    _assert_no_archive_pass(mismatch)
    assert mismatch["STATUS"] == "BLOCKED"
    assert mismatch["REASON"] == "revision_not_current"
    assert mismatch["INDEX_SCAN"] == "not_run"
    assert mismatch["HEAD"] == HEAD_A
    assert mismatch["REVISION"] == HEAD_B

    missing = _review(root, change="missing-change-id")
    assert missing["STATUS"] == "BLOCKED"
    assert missing["REASON"] == "change_not_found"


def test_path_outside_workspace_blocked(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    _init(root)
    outside = tmp_path / "outside.py"
    _ = outside.write_text(f"SECRET_MARKER\n{QUOTE}\n", encoding="utf-8")
    _write_packet(
        root,
        case="SEC-CASE-OUTSIDE01",
        finding="SEC-FIND-OUTSIDE01",
        packet=_packet(
            case="SEC-CASE-OUTSIDE01",
            finding="SEC-FIND-OUTSIDE01",
            signal_hash="hash-outside",
            path=str(outside),
        ),
    )
    result = _review(root)
    _assert_no_archive_pass(result)
    assert result["STATUS"] == "BLOCKED"
    assert result["REASON"] == "path_outside_workspace"
    assert "SECRET_MARKER" not in json.dumps(result)

    sibling_root = tmp_path / "workspace_evil"
    sibling_root.mkdir()
    sibling = sibling_root / "leak.py"
    _ = sibling.write_text("SIBLING_MARKER\n", encoding="utf-8")
    _write_packet(
        root,
        case="SEC-CASE-SIBLING01",
        finding="SEC-FIND-SIBLING01",
        packet=_packet(
            case="SEC-CASE-SIBLING01",
            finding="SEC-FIND-SIBLING01",
            signal_hash="hash-sibling",
            path=str(sibling),
        ),
    )
    sibling_result = _review(root)
    assert sibling_result["REASON"] == "path_outside_workspace"
    assert "SIBLING_MARKER" not in json.dumps(sibling_result)


def test_index_discovers_only_change_bound_confirmed(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    _init(root)
    _write_source(root, "app/payments_psp.py", f"def check(origin):\n    return {QUOTE}\n")
    _write_packet(
        root,
        case="SEC-CASE-BOUND01",
        finding="SEC-FIND-BOUND01",
        packet=_packet(
            case="SEC-CASE-BOUND01",
            finding="SEC-FIND-BOUND01",
            signal_hash="hash-bound",
            path="app/payments_psp.py",
        ),
    )
    _write_packet(
        root,
        case="SEC-CASE-OTHER01",
        finding="SEC-FIND-OTHER01",
        packet=_packet(
            case="SEC-CASE-OTHER01",
            finding="SEC-FIND-OTHER01",
            signal_hash="hash-other",
            path="app/payments_psp.py",
            change="other-change",
        ),
    )
    _write_packet(
        root,
        case="SEC-CASE-UNBOUND01",
        finding="SEC-FIND-UNBOUND01",
        packet=_packet(
            case="SEC-CASE-UNBOUND01",
            finding="SEC-FIND-UNBOUND01",
            signal_hash="hash-unbound",
            path="app/payments_psp.py",
            status="NEEDS_CHANGE_BINDING",
        ),
    )
    result = _review(root)
    assert result["INDEX_HITS"] == ["SEC-FIND-BOUND01"]
    assert result["INDEX_SCAN"] == "complete"
    dispositions = {
        row["SECURITY_FINDING_ID"]: row["FINDING_DISPOSITION"]
        for row in result["FINDINGS"]
    }
    assert dispositions["SEC-FIND-BOUND01"] == "STILL_OPEN"
    assert "SEC-FIND-OTHER01" not in dispositions
    assert "SEC-FIND-UNBOUND01" not in dispositions


def test_stale_revision_is_not_remediated(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    _init(root)
    _write_source(root, "app/payments_psp.py", f"def check(origin):\n    return {FIX_QUOTE}\n")
    _write_packet(
        root,
        case="SEC-CASE-STALE01",
        finding="SEC-FIND-STALE01",
        packet=_packet(
            case="SEC-CASE-STALE01",
            finding="SEC-FIND-STALE01",
            signal_hash="hash-stale",
            path="app/payments_psp.py",
            observed=HEAD_B,
        ),
    )
    result = _review(
        root,
        remediation_evidence={"SEC-FIND-STALE01": [FIX_QUOTE]},
    )
    _assert_no_archive_pass(result)
    assert result["STATUS"] == "DATA_GAP"
    assert result["REASON"] == "stale_revision"
    row = result["FINDINGS"][0]
    assert row["FINDING_DISPOSITION"] == "DATA_GAP"
    assert row["DISPOSITION_REASON"] == "stale_revision"
    assert row["FINDING_DISPOSITION"] != "REMEDIATED"


def test_unnamed_index_finding_is_not_skipped(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    _init(root)
    _write_source(root, "app/payments_psp.py", f"{QUOTE}\n")
    _write_packet(
        root,
        case="SEC-CASE-ONE",
        finding="SEC-FIND-NAMED01",
        packet=_packet(
            case="SEC-CASE-ONE",
            finding="SEC-FIND-NAMED01",
            signal_hash="hash-named",
            path="app/payments_psp.py",
        ),
    )
    _write_packet(
        root,
        case="SEC-CASE-TWO",
        finding="SEC-FIND-QUIET01",
        packet=_packet(
            case="SEC-CASE-TWO",
            finding="SEC-FIND-QUIET01",
            signal_hash="hash-quiet",
            path="app/payments_psp.py",
        ),
    )
    result = _review(root, security_findings=["SEC-FIND-NAMED01"])
    _assert_no_archive_pass(result)
    accounted = {row["SECURITY_FINDING_ID"] for row in result["FINDINGS"]}
    accounted.update(row["SECURITY_FINDING_ID"] for row in result["DUPLICATE_FINDINGS"])
    assert set(result["INDEX_HITS"]) <= accounted
    assert "SEC-FIND-QUIET01" in accounted
    assert result["STATUS"] == "FAIL"


def test_index_scan_opt_out_is_blocked(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    _init(root)
    result = _review(root, index_scan=False)
    _assert_no_archive_pass(result)
    assert result["STATUS"] == "BLOCKED"
    assert result["REASON"] == "index_scan_required"
    assert result["INDEX_SCAN"] == "not_run"


def test_wrong_plane_sets_no_product_verdict(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    _init(root)
    _write_source(root, "app/payments_psp.py", f"{QUOTE}\n")
    _write_packet(
        root,
        case="SEC-CASE-CP01",
        finding="SEC-FIND-CP01",
        packet=_packet(
            case="SEC-CASE-CP01",
            finding="SEC-FIND-CP01",
            signal_hash="hash-cp",
            path="app/payments_psp.py",
            plane="CONTROL_PLANE",
        ),
    )
    _write_packet(
        root,
        case="SEC-CASE-INFRA01",
        finding="SEC-FIND-INFRA01",
        packet=_packet(
            case="SEC-CASE-INFRA01",
            finding="SEC-FIND-INFRA01",
            signal_hash="hash-infra",
            path="app/payments_psp.py",
            plane="INFRASTRUCTURE",
        ),
    )
    result = _review(root)
    _assert_no_archive_pass(result)
    assert result["STATUS"] == "DATA_GAP"
    assert result["REASON"] == "wrong_plane_no_product_verdict"
    assert result["FINDINGS_OPEN"] == []
    for row in result["FINDINGS"]:
        assert row["FINDING_DISPOSITION"] == "OUT_OF_SCOPE"
        assert row["PRODUCT_VERDICT"] == "not_set"
        assert row["DISPOSITION_REASON"] == "wrong_plane"


def test_duplicate_cannot_hide_still_open(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    _init(root)
    _write_source(root, "app/open_case.py", f"{QUOTE}\n")
    _write_source(root, "app/fixed_case.py", f"{FIX_QUOTE}\n")
    shared = "hash-duplicate"
    _write_packet(
        root,
        case="SEC-CASE-DUPA",
        finding="SEC-FIND-DUPFIXED",
        packet=_packet(
            case="SEC-CASE-DUPA",
            finding="SEC-FIND-DUPFIXED",
            signal_hash=shared,
            path="app/fixed_case.py",
        ),
    )
    _write_packet(
        root,
        case="SEC-CASE-DUPB",
        finding="SEC-FIND-DUPOPEN",
        packet=_packet(
            case="SEC-CASE-DUPB",
            finding="SEC-FIND-DUPOPEN",
            signal_hash=shared,
            path="app/open_case.py",
        ),
    )
    result = _review(
        root,
        remediation_evidence={"SEC-FIND-DUPFIXED": [FIX_QUOTE]},
    )
    _assert_no_archive_pass(result)
    assert result["STATUS"] == "FAIL"
    assert len(result["FINDINGS"]) == 1
    assert result["FINDINGS"][0]["FINDING_DISPOSITION"] == "STILL_OPEN"
    assert result["FINDINGS"][0]["INDEPENDENT_EVIDENCE"][0]["QUOTE"] == QUOTE
    assert result["DUPLICATE_FINDINGS"][0]["REASON"] == "duplicate_signal_hash"
    duplicate_ids = {row["SECURITY_FINDING_ID"] for row in result["DUPLICATE_FINDINGS"]}
    assert "SEC-FIND-DUPFIXED" in duplicate_ids
    assert "REMEDIATED" not in {
        row["FINDING_DISPOSITION"] for row in result["FINDINGS"]
    }


def test_no_false_pass_on_empty_or_remediated_index(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    _init(root)
    before = _snapshot(root)
    empty = _review(root)
    _assert_no_archive_pass(empty)
    assert empty["STATUS"] == "DATA_GAP"
    assert empty["REASON"] == "undeclared_surface_coverage"
    assert empty["INDEX_SCAN"] == "complete"
    assert _snapshot(root) == before

    _write_source(root, "app/payments_psp.py", f"def check(origin):\n    return {FIX_QUOTE}\n")
    _write_packet(
        root,
        case="SEC-CASE-FIXED01",
        finding="SEC-FIND-FIXED01",
        packet=_packet(
            case="SEC-CASE-FIXED01",
            finding="SEC-FIND-FIXED01",
            signal_hash="hash-fixed",
            path="app/payments_psp.py",
        ),
    )
    before_fixed = _snapshot(root)
    remediated = _review(
        root,
        remediation_evidence={"SEC-FIND-FIXED01": [FIX_QUOTE]},
    )
    _assert_no_archive_pass(remediated)
    assert remediated["STATUS"] == "DATA_GAP"
    assert remediated["REASON"] == "undeclared_surface_coverage"
    assert remediated["FINDINGS"][0]["FINDING_DISPOSITION"] == "REMEDIATED"
    assert remediated["FINDINGS"][0]["INDEPENDENT_EVIDENCE"][0]["QUOTE"] == FIX_QUOTE
    assert remediated["FINDINGS"][0]["CAUSAL_EVIDENCE_SUFFICIENT"] == "yes"
    assert _snapshot(root) == before_fixed


def test_not_reproduced_and_cited_gap_are_terminal(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    _init(root)
    _write_source(root, "app/payments_psp.py", "def check(origin):\n    return True\n")
    _write_packet(
        root,
        case="SEC-CASE-GONE01",
        finding="SEC-FIND-GONE01",
        packet=_packet(
            case="SEC-CASE-GONE01",
            finding="SEC-FIND-GONE01",
            signal_hash="hash-gone",
            path="app/payments_psp.py",
        ),
    )
    missing_fix = _review(root)
    assert missing_fix["STATUS"] == "DATA_GAP"
    assert missing_fix["REASON"] == "not_reproduced"
    assert missing_fix["FINDINGS"][0]["FINDING_DISPOSITION"] == "NOT_REPRODUCED"

    _ = (
        root / "openspec" / "changes" / CHANGE / "design.md"
    ).write_text("see SEC-FIND-CITED01\n", encoding="utf-8")
    cited = _review(root)
    reasons = {row["DISPOSITION_REASON"] for row in cited["FINDINGS"]}
    assert "finding_not_in_index" in reasons
    assert cited["STATUS"] == "DATA_GAP"
    assert cited["STATUS"] not in FORBIDDEN


def test_corrupt_index_is_data_gap(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    _init(root)
    path = (
        root
        / "opsx"
        / "runs"
        / "security-cases"
        / "SEC-CASE-BAD"
        / "findings"
        / "SEC-FIND-BAD.md"
    )
    path.parent.mkdir(parents=True)
    _ = path.write_text("not a packet\n", encoding="utf-8")
    result = _review(root)
    _assert_no_archive_pass(result)
    assert result["STATUS"] == "DATA_GAP"
    assert result["REASON"] == "index_unreadable"
    assert result["INDEX_SCAN"] == "unreadable"


def test_bounded_binding_gap_has_no_fallback(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    _init(root)
    _write_source(root, "app/payments_psp.py", f"{QUOTE}\n")
    _write_packet(
        root,
        case="SEC-CASE-BOUND01",
        finding="SEC-FIND-BOUND01",
        packet=_packet(
            case="SEC-CASE-BOUND01",
            finding="SEC-FIND-BOUND01",
            signal_hash="hash-bound",
            path="app/payments_psp.py",
        ),
    )
    module = _load()
    unregistered = module.review(
        root,
        {"mode": "bounded", "change": CHANGE, "revision": HEAD_A},
        head_reader=lambda _path: HEAD_A,
        registry_text=REGISTRY_WITHOUT,
    )
    _assert_no_archive_pass(unregistered)
    assert unregistered["STATUS"] == "BLOCKED"
    assert unregistered["REASON"] == "BINDING_GAP"
    assert unregistered["DETAIL"] == "S6_SECURITY_REVIEW_CAPABILITY_UNREGISTERED"
    assert unregistered["INDEX_SCAN"] == "not_run"
    assert unregistered["FINDINGS"] == []
    assert unregistered["FALLBACK"] == "none"

    registered = module.review(
        root,
        {"mode": "bounded"},
        registry_text=REGISTRY_WITH,
    )
    _assert_no_archive_pass(registered)
    assert registered["REASON"] == "BINDING_GAP"
    assert registered["DETAIL"] == "S6_SECURITY_REVIEW_RESULT_WRITER_NOT_INSTALLED"
    assert registered["INDEX_SCAN"] == "not_run"
    assert registered["FINDINGS"] == []


def test_projectization_and_cli_write_nothing(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    module = _load()
    blocked = module.review(
        root,
        {"mode": "report", "change": CHANGE, "revision": HEAD_A},
        head_reader=lambda _path: HEAD_A,
        registry_text=REGISTRY_WITHOUT,
    )
    assert blocked["STATUS"] == "BLOCKED"
    assert blocked["REASON"] == "OPSX_PROJECTIZATION_REQUIRED"

    _init(root)
    request = root / "request.json"
    _ = request.write_text(
        json.dumps({"mode": "report", "change": CHANGE, "revision": HEAD_A}),
        encoding="utf-8",
    )
    before = _snapshot(root)
    completed = subprocess.run(
        [
            sys.executable,
            str(HELPER),
            "--repo-root",
            str(root),
            "--request-file",
            str(request),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 2 or completed.returncode == 0
    if completed.returncode == 0:
        payload = json.loads(completed.stdout)
        _assert_no_archive_pass(payload)
        assert payload["STATUS"] == "BLOCKED"
        assert payload["REASON"] == "revision_not_current" or payload["REASON"] == "head_unreadable"
    after = [item for item in _snapshot(root) if item != "request.json"]
    before_without_request = [item for item in before if item != "request.json"]
    assert after == before_without_request


def test_helper_does_not_write_packets() -> None:
    text = HELPER.read_text(encoding="utf-8")
    assert ".write_text(" not in text
    assert "SECURITY-REVIEW.md" not in text
    assert "check-results" not in text


def test_real_registry_bounded_binding_gap() -> None:
    module = _load()
    result = module.review(REPO, {"mode": "bounded"})
    _assert_no_archive_pass(result)
    assert result["STATUS"] == "BLOCKED"
    assert result["REASON"] == "BINDING_GAP"
    assert result["DETAIL"] == "S6_SECURITY_REVIEW_CAPABILITY_UNREGISTERED"
    assert result["INDEX_SCAN"] == "not_run"
    assert result["FALLBACK"] == "none"
    assert result["SECURITY_REVIEW_DECLARED"] == "NO"


def test_parent_traversal_denied(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    _init(root)
    outside = tmp_path / "outside.py"
    _ = outside.write_text("TRAVERSAL_MARKER\n", encoding="utf-8")
    _write_packet(
        root,
        case="SEC-CASE-TRAV01",
        finding="SEC-FIND-TRAV01",
        packet=_packet(
            case="SEC-CASE-TRAV01",
            finding="SEC-FIND-TRAV01",
            signal_hash="hash-travel",
            path="../outside.py",
        ),
    )
    result = _review(root)
    assert result["STATUS"] == "BLOCKED"
    assert result["REASON"] == "path_outside_workspace"
    assert "TRAVERSAL_MARKER" not in json.dumps(result)


def test_symlink_escape_denied(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    _init(root)
    outside = tmp_path / "linked.py"
    _ = outside.write_text("LINK_MARKER\n", encoding="utf-8")
    link = root / "app"
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(tmp_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0 or not link.exists():
            pytest.skip("junction creation is not permitted")
    else:
        try:
            link.symlink_to(tmp_path, target_is_directory=True)
        except OSError:
            pytest.skip("symlink creation is not permitted")
    _write_packet(
        root,
        case="SEC-CASE-LINK01",
        finding="SEC-FIND-LINK01",
        packet=_packet(
            case="SEC-CASE-LINK01",
            finding="SEC-FIND-LINK01",
            signal_hash="hash-link",
            path="app/linked.py",
        ),
    )
    result = _review(root)
    assert result["STATUS"] == "BLOCKED"
    assert result["REASON"] == "path_outside_workspace"
    assert "LINK_MARKER" not in json.dumps(result)
