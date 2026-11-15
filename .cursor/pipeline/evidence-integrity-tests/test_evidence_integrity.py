"""Синтетические git-репозитории для приёмки S-EV1 (таблица owner gate)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "evidence-integrity" / "evidence_integrity.py"
)
CHANGE = "openspec/changes/demo-change"
SLICE = "SLICE-001"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("evidence_integrity", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


EV = _load()


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "ev1@test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "EV1 Test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return repo


def _commit_all(repo: Path, message: str) -> str:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return _git(repo, "rev-parse", "HEAD")


def _write(repo: Path, rel: str, content: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _base_input(repo: Path, contour: str = "product") -> dict[str, object]:
    return {
        "schema": "opsx.s_ev1_input",
        "schema_version": 1,
        "repo_root": str(repo),
        "change_root": CHANGE,
        "contour": contour,
        "slice_id": SLICE,
    }


def _design_with_surfaces() -> str:
    block = json.dumps(
        {
            "evidence_integrity_production_surfaces": {
                SLICE: ["src/product.py"],
            }
        },
        indent=2,
    )
    return f"# design\n\n```json\n{block}\n```\n"


def _red_json(status: str = "ALREADY_SATISFIED", **extra: object) -> str:
    data: dict[str, object] = {
        "schema": "opsx.apply_red",
        "slice_id": SLICE,
        "status": status,
        "test_paths": ["tests/test_slice.py"],
        "fixture_paths": [],
        "test_content_identity": "abc123",
    }
    data.update(extra)
    return json.dumps(data, indent=2)


def _green_json(red_status: str, **extra: object) -> str:
    data: dict[str, object] = {
        "schema": "opsx.apply_green",
        "slice_id": SLICE,
        "predecessor_branch": "ADOPTION",
        "status": "GREEN_PROVEN",
        "pre_impl_revision": {"commit_oid": "0" * 40},
        "green_input_key": "deadbeef",
        "red_identity": {
            "status": red_status,
            "test_content_identity": "abc123",
        },
    }
    data.update(extra)
    return json.dumps(data, indent=2)


def _codes(report: dict[str, object]) -> set[str]:
    findings = report.get("findings", [])
    return {str(f["code"]) for f in findings}


def _run(repo: Path, contour: str = "product", **kwargs: object) -> dict[str, object]:
    payload = _base_input(repo, contour=contour)
    payload.update(kwargs)
    return EV.verify_s_ev1(payload)


def test_ei_n01_cross_receipt_mismatch_introducing_lie(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(repo, f"{CHANGE}/design.md", _design_with_surfaces())
    _write(repo, "tests/test_slice.py", "def test_x(): pass\n")
    _write(
        repo,
        f"{CHANGE}/apply-red/{SLICE}/RED.json",
        _red_json("ALREADY_SATISFIED"),
    )
    _write(
        repo,
        f"{CHANGE}/apply-green/{SLICE}/GREEN.json",
        _green_json("RED_PROVEN"),
    )
    _commit_all(repo, "introduce mismatch")
    report = _run(repo)
    assert "CROSS_RECEIPT_STATUS_MISMATCH" in _codes(report)
    assert "STATUS_RESTAMP" not in _codes(report)


def test_ei_n01b_mismatch_plus_mutation(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(repo, f"{CHANGE}/design.md", _design_with_surfaces())
    _write(repo, "tests/test_slice.py", "def test_x(): pass\n")
    _write(repo, f"{CHANGE}/apply-red/{SLICE}/RED.json", _red_json("ALREADY_SATISFIED"))
    _write(
        repo,
        f"{CHANGE}/apply-green/{SLICE}/GREEN.json",
        _green_json("ALREADY_SATISFIED"),
    )
    _commit_all(repo, "consistent")
    green_path = repo / CHANGE / "apply-green" / SLICE / "GREEN.json"
    data = json.loads(green_path.read_text(encoding="utf-8"))
    data["red_identity"]["status"] = "RED_PROVEN"
    green_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _commit_all(repo, "edit green")
    report = _run(repo)
    codes = _codes(report)
    assert "CROSS_RECEIPT_STATUS_MISMATCH" in codes
    assert "RECEIPT_MUTATED_AFTER_STAGE" in codes
    assert "STATUS_RESTAMP" in codes


def test_ei_n06_bad_attestation_ref(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(repo, f"{CHANGE}/design.md", _design_with_surfaces())
    auth = {
        "owner_attestation_ref": "openspec/changes/forged.json",
        "content_hash": "x",
    }
    _write(
        repo,
        f"{CHANGE}/apply-implement/{SLICE}/CURRENT-BYTE-ADOPTION-AUTHORITY-x.json",
        json.dumps(auth),
    )
    _commit_all(repo, "authority")
    report = _run(repo)
    assert "ATTESTATION_REF_NOT_OWNER_HELD" in _codes(report)


def test_ei_n06b_good_attestation_prefix(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(repo, f"{CHANGE}/design.md", _design_with_surfaces())
    for ref in (
        "store:bc/owner-go",
        "cursor-stores/bc/owner-go",
        "/cursor/stores/bc/owner-go",
        "owner-message:abc",
    ):
        auth = {"owner_attestation_ref": ref}
        _write(
            repo,
            f"{CHANGE}/apply-implement/{SLICE}/CURRENT-BYTE-ADOPTION-AUTHORITY-{ref[:8]}.json",
            json.dumps(auth),
        )
    _commit_all(repo, "authority ok")
    report = _run(repo)
    codes = _codes(report)
    assert "ATTESTATION_REF_NOT_OWNER_HELD" not in codes
    assert "ATTESTATION_CONTENT_UNVERIFIED" in codes
    assert report["max_tier_reached"] in ("T0", "T1")


def test_ei_n08_status_restamp(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(repo, f"{CHANGE}/design.md", _design_with_surfaces())
    _write(repo, f"{CHANGE}/apply-red/{SLICE}/RED.json", _red_json("ALREADY_SATISFIED"))
    _commit_all(repo, "red")
    red_path = repo / CHANGE / "apply-red" / SLICE / "RED.json"
    data = json.loads(red_path.read_text(encoding="utf-8"))
    data["status"] = "RED_PROVEN"
    red_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _commit_all(repo, "restamp")
    report = _run(repo)
    codes = _codes(report)
    assert "RECEIPT_MUTATED_AFTER_STAGE" in codes
    assert "STATUS_RESTAMP" in codes


def test_ei_n08b_supersedes_does_not_clear_mutation(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(repo, f"{CHANGE}/design.md", _design_with_surfaces())
    _write(repo, f"{CHANGE}/apply-red/{SLICE}/RED.json", _red_json("ALREADY_SATISFIED"))
    _commit_all(repo, "red")
    red_path = repo / CHANGE / "apply-red" / SLICE / "RED.json"
    data = json.loads(red_path.read_text(encoding="utf-8"))
    data["status"] = "RED_PROVEN"
    data["supersedes"] = "oldsha"
    data["epoch"] = 2
    red_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _commit_all(repo, "supersede keys")
    assert "RECEIPT_MUTATED_AFTER_STAGE" in _codes(_run(repo))


def test_ei_n09_red_commit_touches_green(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(repo, f"{CHANGE}/design.md", _design_with_surfaces())
    _write(repo, "tests/test_slice.py", "x")
    _write(repo, f"{CHANGE}/apply-red/{SLICE}/RED.json", _red_json())
    _write(repo, f"{CHANGE}/apply-green/{SLICE}/GREEN.json", _green_json("ALREADY_SATISFIED"))
    _commit_all(repo, "red+green same commit")
    assert "STAGE_BOUNDARY_VIOLATION" in _codes(_run(repo))


def test_ei_n10_implement_touches_tests(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(repo, f"{CHANGE}/design.md", _design_with_surfaces())
    _write(repo, "tests/test_slice.py", "v1")
    _write(repo, f"{CHANGE}/apply-red/{SLICE}/RED.json", _red_json())
    _commit_all(repo, "red")
    _write(repo, "tests/test_slice.py", "v2")
    _write(
        repo,
        f"{CHANGE}/apply-implement/{SLICE}/IMPLEMENTATION.json",
        json.dumps({"status": "IMPLEMENTATION_ADOPTED", "slice_id": SLICE}),
    )
    _commit_all(repo, "implement test change")
    codes = _codes(_run(repo))
    assert "STAGE_BOUNDARY_VIOLATION" in codes
    assert "FROZEN_RED_MUTATED" in codes


def test_ei_n14_digest_rewrite(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(repo, f"{CHANGE}/design.md", _design_with_surfaces())
    _write(repo, f"{CHANGE}/apply-red/{SLICE}/RED.json", _red_json())
    _commit_all(repo, "red")
    red_path = repo / CHANGE / "apply-red" / SLICE / "RED.json"
    data = json.loads(red_path.read_text(encoding="utf-8"))
    data["test_content_identity"] = "rewritten"
    red_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _commit_all(repo, "rewrite digest")
    codes = _codes(_run(repo))
    assert "RED_IDENTITY_REWRITE" in codes
    assert "RECEIPT_MUTATED_AFTER_STAGE" in codes


def test_ei_n19_overclaim(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(repo, f"{CHANGE}/design.md", _design_with_surfaces())
    bad = _green_json("ALREADY_SATISFIED", verdict_note="PLATFORM_ATTESTED ok")
    _write(repo, f"{CHANGE}/apply-green/{SLICE}/GREEN.json", bad)
    _commit_all(repo, "overclaim")
    report = _run(repo)
    assert "OVERCLAIM_VERDICT" in _codes(report)
    assert report["platform_attestation"] == "UNPROVEN"


def test_ei_n19b_verified_at_tier_t2(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(repo, f"{CHANGE}/design.md", _design_with_surfaces())
    bad = _green_json("ALREADY_SATISFIED", note="VERIFIED_AT_TIER_T2")
    _write(repo, f"{CHANGE}/apply-green/{SLICE}/GREEN.json", bad)
    _commit_all(repo, "t2 string")
    assert "OVERCLAIM_VERDICT" in _codes(_run(repo))


def test_ei_n20_format_valid_only(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(repo, f"{CHANGE}/design.md", _design_with_surfaces())
    data = json.loads(_green_json("ALREADY_SATISFIED"))
    data["self_asserted_strong_verdict"] = True
    _write(repo, f"{CHANGE}/apply-green/{SLICE}/GREEN.json", json.dumps(data))
    _commit_all(repo, "self asserted")
    report = _run(repo)
    assert "FORMAT_VALID_ONLY" in _codes(report)
    assert report["max_tier_reached"] == "T0"


def test_ei_n23_live_authority(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(repo, f"{CHANGE}/design.md", _design_with_surfaces())
    data = json.loads(_green_json("ALREADY_SATISFIED"))
    data["live_authority"] = "YES"
    _write(repo, f"{CHANGE}/apply-green/{SLICE}/GREEN.json", json.dumps(data))
    _commit_all(repo, "live")
    assert "LIVE_AUTHORITY_CLAIM_FORBIDDEN" in _codes(_run(repo))


def test_ei_n26_product_contour_opsx(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(repo, f"{CHANGE}/design.md", _design_with_surfaces())
    _write(repo, f"{CHANGE}/apply-red/{SLICE}/RED.json", _red_json())
    _write(repo, "opsx_tools/hack.mjs", "// x")
    _commit_all(repo, "opsx touch")
    assert "STAGE_BOUNDARY_VIOLATION" in _codes(_run(repo, contour="product"))


def test_sev1_n_range(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(repo, f"{CHANGE}/design.md", _design_with_surfaces())
    _write(repo, f"{CHANGE}/apply-red/{SLICE}/RED.json", _red_json())
    first = _commit_all(repo, "good")
    red_path = repo / CHANGE / "apply-red" / SLICE / "RED.json"
    data = json.loads(red_path.read_text(encoding="utf-8"))
    data["status"] = "RED_PROVEN"
    red_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    bad = _commit_all(repo, "bad restamp")
    payload = _base_input(repo)
    report = EV.verify_s_ev1(payload, narrowed_commits=[first])
    assert "RANGE_NARROWED" in _codes(report)
    assert "STATUS_RESTAMP" in _codes(report)


def test_sev1_n_allow_unbound(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(repo, f"{CHANGE}/design.md", "# no surfaces\n")
    _write(repo, "src/product.py", "x=1\n")
    _write(
        repo,
        f"{CHANGE}/apply-implement/{SLICE}/IMPLEMENTATION.json",
        json.dumps({"slice_id": SLICE}),
    )
    _commit_all(repo, "implement src without surfaces")
    codes = _codes(_run(repo))
    assert "ALLOWLIST_UNBOUND" in codes
    assert "STAGE_BOUNDARY_VIOLATION" not in codes


def test_sev1_p_idempotent(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(repo, f"{CHANGE}/design.md", _design_with_surfaces())
    _write(repo, "tests/test_slice.py", "def test_x(): pass\n")
    _write(repo, f"{CHANGE}/apply-red/{SLICE}/RED.json", _red_json("ALREADY_SATISFIED"))
    _write(
        repo,
        f"{CHANGE}/apply-green/{SLICE}/GREEN.json",
        _green_json("ALREADY_SATISFIED"),
    )
    _commit_all(repo, "stable")
    a = json.dumps(_run(repo), sort_keys=True)
    b = json.dumps(_run(repo), sort_keys=True)
    assert a == b
    assert _run(repo)["writes"] == []


def test_sev1_p_stable_red_boundary(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(repo, f"{CHANGE}/design.md", _design_with_surfaces())
    _write(repo, "tests/test_slice.py", "def test_x(): pass\n")
    _write(repo, f"{CHANGE}/apply-red/{SLICE}/RED.json", _red_json("ALREADY_SATISFIED"))
    _write(repo, f"{CHANGE}/apply-red/{SLICE}/RED.md", "# red\n")
    _commit_all(repo, "red only")
    codes = _codes(_run(repo))
    assert "RECEIPT_MUTATED_AFTER_STAGE" not in codes
    assert "STAGE_BOUNDARY_VIOLATION" not in codes


def test_sev1_p_agree_no_mismatch(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(repo, f"{CHANGE}/design.md", _design_with_surfaces())
    _write(repo, f"{CHANGE}/apply-red/{SLICE}/RED.json", _red_json("ALREADY_SATISFIED"))
    _write(
        repo,
        f"{CHANGE}/apply-green/{SLICE}/GREEN.json",
        _green_json("ALREADY_SATISFIED"),
    )
    _commit_all(repo, "agree")
    assert "CROSS_RECEIPT_STATUS_MISMATCH" not in _codes(_run(repo))


def test_sev1_p_control_plane_pipeline(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(repo, f"{CHANGE}/design.md", _design_with_surfaces())
    _write(repo, f"{CHANGE}/apply-red/{SLICE}/RED.json", _red_json())
    _write(repo, ".cursor/pipeline/evidence-integrity/note.txt", "ok")
    _commit_all(repo, "pipeline touch")
    codes = _codes(_run(repo, contour="control_plane"))
    assert "STAGE_BOUNDARY_VIOLATION" not in codes


def test_cli_exit_zero_with_findings(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    _write(repo, f"{CHANGE}/design.md", _design_with_surfaces())
    _write(
        repo,
        f"{CHANGE}/apply-green/{SLICE}/GREEN.json",
        _green_json("RED_PROVEN"),
    )
    _commit_all(repo, "green only mismatch path")
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps(_base_input(repo)), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(MODULE_PATH), "--input", str(inp)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    out = json.loads(completed.stdout)
    assert out["writes"] == []
