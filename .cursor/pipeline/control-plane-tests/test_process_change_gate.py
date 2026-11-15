"""Contract tests for OPSX process-change gate (ECR-LITE V0)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
GATE = REPO_ROOT / "tools" / "openspec" / "process_change_gate.py"

sys.path.insert(0, str(REPO_ROOT / "tools" / "openspec"))
from process_change_gate import (  # noqa: E402
    ProcessChangeGateError,
    evaluate_gate,
    set_repo_root,
    validate_ecr_lite_schema,
    validate_review_d3,
)


def _run_git(args: list[str], cwd: Path) -> None:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr


def _init_minimal_repo(tmp_path: Path) -> Path:
    """Bootstrap a tiny git repo with one process-surface file."""
    _run_git(["init"], tmp_path)
    _run_git(["config", "user.email", "test@example.com"], tmp_path)
    _run_git(["config", "user.name", "test"], tmp_path)
    cmd = tmp_path / ".cursor" / "commands"
    cmd.mkdir(parents=True)
    (cmd / "sample.md").write_text("# sample\n", encoding="utf-8")
    schemas = tmp_path / "opsx" / "schemas"
    schemas.mkdir(parents=True)
    src_manifest = REPO_ROOT / "opsx/schemas/process-surfaces-v0.json"
    (schemas / "process-surfaces-v0.json").write_text(
        src_manifest.read_text(encoding="utf-8"), encoding="utf-8"
    )
    _run_git(["add", "."], tmp_path)
    _run_git(["commit", "-m", "baseline"], tmp_path)
    return tmp_path


def _write_ecr(tmp_path: Path, pcp_id: str, revision_sha: str, author: str, reviewer: str | None) -> Path:
    pcp_dir = tmp_path / "opsx" / "process-changes" / pcp_id
    pcp_dir.mkdir(parents=True, exist_ok=True)
    attestations: list[dict[str, str]] = []
    if reviewer:
        review_path = tmp_path / "opsx" / "runs" / "review" / "attestation.json"
        review_path.parent.mkdir(parents=True, exist_ok=True)
        review_path.write_text(
            json.dumps(
                {
                    "schema_version": "opsx-process-review-attestation-v0",
                    "pcp_id": pcp_id,
                    "reviewer_principal": reviewer,
                    "author_principal": author,
                    "attestation_type": "chat_command_audit",
                    "revision_sha": revision_sha,
                    "scope": [".cursor/commands/sample.md"],
                    "verdict": "PASS",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        attestations.append(
            {
                "reviewer_principal": reviewer,
                "attestation_type": "chat_command_audit",
                "evidence_path": "opsx/runs/review/attestation.json",
            }
        )
    doc = {
        "schema_version": "opsx-ecr-lite-v0",
        "pcp_id": pcp_id,
        "pcp_tier": "LITE",
        "revision_sha": revision_sha,
        "why": "test fixture",
        "what_paths": [".cursor/commands/sample.md"],
        "author_principal": author,
        "integration": {"promotion_target": "dev"},
        "impact": {
            "closure": {"status": "PASS", "predicate_version": "v0.1.4"},
        },
        "evidence_refs": [{"kind": "l3_closure", "path": "opsx/runs/process-change-gate/x.json"}],
        "review": {"attestations": attestations},
        "required_tests": [
            {"test_id": "test_opsx_dependency_closure", "evidence_ref": "ci:local"},
        ],
    }
    path = pcp_dir / "ecr-lite.json"
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return path


def test_schema_rejects_missing_t1() -> None:
    bad = {
        "schema_version": "opsx-ecr-lite-v0",
        "pcp_id": "PCP-20261025-t",
        "pcp_tier": "LITE",
        "revision_sha": "a" * 40,
        "why": "x",
        "what_paths": [".cursor/commands/x.md"],
        "author_principal": "a",
        "integration": {"promotion_target": "dev"},
        "impact": {"closure": {"status": "PASS", "predicate_version": "v0.1.4"}},
        "evidence_refs": [{"kind": "l3_closure", "path": "p"}],
        "review": {"attestations": []},
        "required_tests": [],
    }
    with pytest.raises(ProcessChangeGateError, match="required_tests"):
        validate_ecr_lite_schema(bad)


def test_at1_missing_ecr_fails(tmp_path: Path) -> None:
    root = _init_minimal_repo(tmp_path)
    set_repo_root(root)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    sample = root / ".cursor/commands/sample.md"
    sample.write_text("# sample\nchanged\n", encoding="utf-8")
    _run_git(["add", sample], root)
    _run_git(["commit", "-m", "touch command"], root)
    parent = subprocess.check_output(["git", "rev-parse", "HEAD~1"], cwd=root, text=True).strip()
    with pytest.raises(ProcessChangeGateError, match="AT-1"):
        evaluate_gate(
            base=parent,
            head="HEAD",
            root=root,
            run_pytest=False,
            check_meta_commits=False,
        )


def test_at2bis_two_commit_metadata_bind_passes_ci_enforcement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AT-2bis: revision_sha may reference input commit when only metadata changed after."""
    root = _init_minimal_repo(tmp_path)
    set_repo_root(root)
    sample = root / ".cursor/commands/sample.md"
    sample.write_text("# sample\nv3\n", encoding="utf-8")
    input_parent = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    _write_ecr(root, "PCP-20261025-t2bis", input_parent, "author", "reviewer")
    _run_git(["add", "."], root)
    _run_git(["commit", "-m", "process + ecr input revision"], root)
    input_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    ecr_path = root / "opsx/process-changes/PCP-20261025-t2bis/ecr-lite.json"
    review_path = root / "opsx/runs/review/attestation.json"
    doc = json.loads(ecr_path.read_text(encoding="utf-8"))
    doc["revision_sha"] = input_sha
    ecr_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    att = json.loads(review_path.read_text(encoding="utf-8"))
    att["revision_sha"] = input_sha
    review_path.write_text(json.dumps(att, indent=2) + "\n", encoding="utf-8")
    _run_git(["add", ecr_path, review_path], root)
    _run_git(["commit", "-m", "bind metadata only"], root)

    def _fake_closure(_root: Path) -> dict:
        return {"status": "PASS", "global_failures": [], "command_blocks": []}

    monkeypatch.setattr("process_change_gate.evaluate_dependency_closure", _fake_closure)
    parent = subprocess.check_output(["git", "rev-parse", "HEAD~1"], cwd=root, text=True).strip()
    result = evaluate_gate(
        base=parent,
        head="HEAD",
        root=root,
        run_pytest=False,
        check_meta_commits=False,
        enforce_revision_sha=True,
        phase="ci_pr",
    )
    assert result.status == "PASS"


def test_at2bis_stale_process_edit_after_binding_fails(tmp_path: Path) -> None:
    root = _init_minimal_repo(tmp_path)
    set_repo_root(root)
    sample = root / ".cursor/commands/sample.md"
    sample.write_text("# sample\nv1\n", encoding="utf-8")
    input_parent = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    _write_ecr(root, "PCP-20261025-stale", input_parent, "author", "reviewer")
    _run_git(["add", "."], root)
    _run_git(["commit", "-m", "initial"], root)
    input_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    sample.write_text("# sample\nv2\n", encoding="utf-8")
    ecr_path = root / "opsx/process-changes/PCP-20261025-stale/ecr-lite.json"
    doc = json.loads(ecr_path.read_text(encoding="utf-8"))
    doc["revision_sha"] = input_sha
    ecr_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    _run_git(["add", sample, ecr_path], root)
    _run_git(["commit", "-m", "edit after bind"], root)
    parent = subprocess.check_output(["git", "rev-parse", "HEAD~1"], cwd=root, text=True).strip()
    with pytest.raises(ProcessChangeGateError, match="AT-2bis"):
        evaluate_gate(
            base=parent,
            head="HEAD",
            root=root,
            run_pytest=False,
            check_meta_commits=False,
            enforce_revision_sha=True,
            phase="ci_pr",
        )


def test_at2_revision_sha_mismatch(tmp_path: Path) -> None:
    root = _init_minimal_repo(tmp_path)
    set_repo_root(root)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    sample = root / ".cursor/commands/sample.md"
    sample.write_text("# sample\nv3\n", encoding="utf-8")
    _write_ecr(root, "PCP-20261025-t2", "b" * 40, "author", "reviewer")
    _run_git(["add", "."], root)
    _run_git(["commit", "-m", "add ecr"], root)
    parent = subprocess.check_output(["git", "rev-parse", "HEAD~1"], cwd=root, text=True).strip()
    with pytest.raises(ProcessChangeGateError, match="AT-2"):
        evaluate_gate(
            base=parent,
            head="HEAD",
            root=root,
            run_pytest=False,
            check_meta_commits=False,
        )


def test_schema_only_change_not_not_applicable(tmp_path: Path) -> None:
    """Schema-only process edits must not bypass gate via NOT_APPLICABLE (Finding B)."""
    root = _init_minimal_repo(tmp_path)
    set_repo_root(root)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    schema = root / "opsx/schemas/ecr-lite.schema.json"
    schema.write_text('{"schema_version": "opsx-ecr-lite-v0"}\n', encoding="utf-8")
    _run_git(["add", schema], root)
    _run_git(["commit", "-m", "schema only"], root)
    parent = subprocess.check_output(["git", "rev-parse", "HEAD~1"], cwd=root, text=True).strip()
    with pytest.raises(ProcessChangeGateError, match="AT-1"):
        evaluate_gate(
            base=parent,
            head="HEAD",
            root=root,
            run_pytest=False,
            check_meta_commits=False,
        )


def test_policy_only_change_not_not_applicable(tmp_path: Path) -> None:
    root = _init_minimal_repo(tmp_path)
    set_repo_root(root)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    policy = root / "opsx/policies/process-review-waiver-v0.md"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text("waiver_authorized: false\n", encoding="utf-8")
    _run_git(["add", policy], root)
    _run_git(["commit", "-m", "policy only"], root)
    parent = subprocess.check_output(["git", "rev-parse", "HEAD~1"], cwd=root, text=True).strip()
    with pytest.raises(ProcessChangeGateError, match="AT-1"):
        evaluate_gate(
            base=parent,
            head="HEAD",
            root=root,
            run_pytest=False,
            check_meta_commits=False,
        )


def test_d3_unbound_attestation_fails_in_ci_phase(tmp_path: Path) -> None:
    root = _init_minimal_repo(tmp_path)
    set_repo_root(root)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    sample = root / ".cursor/commands/sample.md"
    sample.write_text("# sample\nv4\n", encoding="utf-8")
    head_before = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    pcp_id = "PCP-20261025-d3unbound"
    review_path = root / "opsx/runs/review/attestation.json"
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text(
        json.dumps(
            {
                "schema_version": "opsx-process-review-attestation-v0",
                "pcp_id": pcp_id,
                "reviewer_principal": "reviewer",
                "author_principal": "author",
                "attestation_type": "chat_command_audit",
                "scope": [".cursor/commands/sample.md"],
                "verdict": "PASS",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _write_ecr(root, pcp_id, head_before, "author", None)
    ecr_pre = root / "opsx/process-changes" / pcp_id / "ecr-lite.json"
    doc_pre = json.loads(ecr_pre.read_text(encoding="utf-8"))
    doc_pre["review"]["attestations"] = [
        {
            "reviewer_principal": "reviewer",
            "attestation_type": "chat_command_audit",
            "evidence_path": "opsx/runs/review/attestation.json",
        }
    ]
    ecr_pre.write_text(json.dumps(doc_pre, indent=2) + "\n", encoding="utf-8")
    _run_git(["add", "."], root)
    _run_git(["commit", "-m", "d3 unbound"], root)
    parent = subprocess.check_output(["git", "rev-parse", "HEAD~1"], cwd=root, text=True).strip()
    head_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    ecr_path = root / "opsx/process-changes" / pcp_id / "ecr-lite.json"
    doc = json.loads(ecr_path.read_text(encoding="utf-8"))
    doc["revision_sha"] = head_sha
    ecr_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ProcessChangeGateError, match="AT-D3 unverified"):
        validate_review_d3(
            doc,
            True,
            head_sha=head_sha,
            require_revision_binding=True,
        )


def test_d3_self_approval_fails(tmp_path: Path) -> None:
    root = _init_minimal_repo(tmp_path)
    set_repo_root(root)
    review_path = root / "opsx/runs/review/attestation.json"
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text("{}\n", encoding="utf-8")
    ecr = {
        "author_principal": "same",
        "review": {
            "attestations": [
                {
                    "reviewer_principal": "same",
                    "attestation_type": "chat_command_audit",
                    "evidence_path": "opsx/runs/review/attestation.json",
                }
            ]
        },
    }
    with pytest.raises(ProcessChangeGateError, match="D3"):
        validate_review_d3(ecr, commands_changed=True, require_revision_binding=False)


def test_d3b_waiver_without_authorized_policy(tmp_path: Path) -> None:
    root = _init_minimal_repo(tmp_path)
    set_repo_root(root)
    ecr = {
        "author_principal": "author",
        "review": {
            "attestations": [],
            "waiver": {
                "scoped_policy_id": "process-review-waiver-v0",
                "waiver_owner_principal": "owner@example.com",
                "reason": "test",
            },
        },
    }
    policy = root / "opsx/policies/process-review-waiver-v0.md"
    policy.parent.mkdir(parents=True, exist_ok=True)
    policy.write_text("waiver_authorized: false\n", encoding="utf-8")
    # validate_review_d3 uses repo_root() — copy policy into real repo is already false;
    # expect WAIVER_NOT_AUTHORIZED when policy exists in workspace.
    with pytest.raises(ProcessChangeGateError, match="active waiver"):
        validate_review_d3(ecr, commands_changed=True, require_revision_binding=False)


def test_not_applicable_non_process_diff(tmp_path: Path) -> None:
    root = _init_minimal_repo(tmp_path)
    set_repo_root(root)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    app = root / "app"
    app.mkdir()
    (app / "main.py").write_text("print('x')\n", encoding="utf-8")
    _run_git(["add", app], root)
    _run_git(["commit", "-m", "product only"], root)
    result = evaluate_gate(
        base=base,
        head="HEAD",
        root=root,
        run_pytest=False,
        check_meta_commits=False,
    )
    assert result.status == "NOT_APPLICABLE"


def test_positive_gate_with_mocked_closure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _init_minimal_repo(tmp_path)
    set_repo_root(root)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    sample = root / ".cursor/commands/sample.md"
    sample.write_text("# sample\nok\n", encoding="utf-8")
    head_before = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    _write_ecr(root, "PCP-20261025-ok", head_before, "author", "reviewer")
    _run_git(["add", "."], root)
    _run_git(
        [
            "commit",
            "-m",
            "chore(opsx-process): fixture (fixture)\n\nPCP-ID: PCP-20261025-ok\n"
            "ECR: opsx/process-changes/PCP-20261025-ok/ecr-lite.json",
        ],
        root,
    )
    ecr_path = root / "opsx/process-changes/PCP-20261025-ok/ecr-lite.json"
    review_path = root / "opsx/runs/review/attestation.json"
    input_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    doc = json.loads(ecr_path.read_text(encoding="utf-8"))
    doc["revision_sha"] = input_sha
    ecr_path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    att = json.loads(review_path.read_text(encoding="utf-8"))
    att["revision_sha"] = input_sha
    review_path.write_text(json.dumps(att, indent=2) + "\n", encoding="utf-8")
    _run_git(["add", ecr_path, review_path], root)
    _run_git(
        [
            "commit",
            "-m",
            "chore(opsx-process): bind metadata (fixture)\n\nPCP-ID: PCP-20261025-ok\n"
            "ECR: opsx/process-changes/PCP-20261025-ok/ecr-lite.json",
        ],
        root,
    )

    def _fake_closure(_root: Path) -> dict:
        return {
            "status": "PASS",
            "global_failures": [],
            "command_blocks": [],
        }

    monkeypatch.setattr(
        "process_change_gate.evaluate_dependency_closure",
        _fake_closure,
    )
    parent = subprocess.check_output(["git", "rev-parse", "HEAD~1"], cwd=root, text=True).strip()
    result = evaluate_gate(
        base=parent,
        head="HEAD",
        root=root,
        run_pytest=False,
        check_meta_commits=True,
        enforce_revision_sha=True,
        phase="ci_pr",
    )
    assert result.status == "PASS"
    ecr_doc = json.loads(ecr_path.read_text(encoding="utf-8"))
    assert validate_review_d3(
        ecr_doc,
        True,
        head_sha=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        require_revision_binding=True,
    ) == "REVISION_BOUND"
    assert result.pcp_id == "PCP-20261025-ok"
    assert "CAPABILITY_WIRED" in result.lifecycle_labels


def test_repo_positive_gate_skips_pytest_meta() -> None:
    """Smoke: gate module loads and NOT_APPLICABLE on empty diff against self."""
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()
    result = evaluate_gate(
        base=head,
        head=head,
        root=REPO_ROOT,
        run_pytest=False,
        check_meta_commits=False,
    )
    assert result.status == "NOT_APPLICABLE"


def test_cli_negative_exit_code(tmp_path: Path) -> None:
    root = _init_minimal_repo(tmp_path)
    base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    sample = root / ".cursor/commands/sample.md"
    sample.write_text("change\n", encoding="utf-8")
    _run_git(["add", sample], root)
    _run_git(["commit", "-m", "no ecr"], root)
    parent = subprocess.check_output(["git", "rev-parse", "HEAD~1"], cwd=root, text=True).strip()
    proc = subprocess.run(
        [
            sys.executable,
            str(GATE),
            "ci-gate",
            "--base",
            parent,
            "--head",
            "HEAD",
            "--skip-pytest",
            "--root",
            str(root),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1
    assert "AT-1" in proc.stderr
