"""Adversarial tests: central mutation authority coverage and bypass resistance."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from conftest import write_minimal_projectization, write_verified_adoption
from tools.openspec.opsx_mutation_authority import (
    REGISTERED_ENTRYPOINTS,
    enforce_mutation_authority,
    load_entrypoint_matrix,
)

from engineering_adoption import (  # type: ignore[import-not-found]
    OWNER_APPROVE_PREFIX,
    policy_proposal_content_sha256,
    transition,
)
from unittest import mock


def _mini_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    write_minimal_projectization(root)
    return root


def test_matrix_matches_registered_entrypoints() -> None:
    matrix = load_entrypoint_matrix(REPO_ROOT)
    ids = {item["id"] for item in matrix.get("entrypoints", []) if item.get("mutation_capable")}
    assert ids == set(REGISTERED_ENTRYPOINTS)


def test_unregistered_entrypoint_fails_closed(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    result = enforce_mutation_authority(root, "node.unknown.writer")
    assert not result.ok
    assert result.reason == "MUTATION_ENTRYPOINT_UNREGISTERED"


def test_all_registered_entrypoints_block_when_unadopted(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    for entrypoint_id in REGISTERED_ENTRYPOINTS:
        result = enforce_mutation_authority(root, entrypoint_id)
        assert not result.ok, entrypoint_id
        assert result.reason == "OPSX_ADOPTION_REQUIRED", entrypoint_id


def test_adoption_restores_registered_entrypoints(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    write_verified_adoption(root)
    for entrypoint_id in REGISTERED_ENTRYPOINTS:
        result = enforce_mutation_authority(root, entrypoint_id)
        assert result.ok, f"{entrypoint_id}: {result.reason} {result.detail}"


def test_node_mutation_authority_cli_blocks_unadopted(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    write_minimal_projectization(root)
    import os

    env = {**os.environ, "OPSX_WORKSPACE_ROOT": str(REPO_ROOT)}
    proc = subprocess.run(
        [
            "node",
            str(REPO_ROOT / "opsx_tools/opsx-mutation-authority.mjs"),
            "node.apply_change_writer.acquire",
            "--root",
            str(root),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert proc.returncode == 1
    payload = json.loads(proc.stdout.strip())
    assert payload.get("REASON") == "OPSX_ADOPTION_REQUIRED"


def test_owner_policy_bare_token_rejected(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    for step in ("DISCOVER", "ASSESS", "POLICY_PROPOSED"):
        assert transition(root, step).ok
    tr = transition(root, "OWNER_POLICY_GATE", owner_token="OWNER_POLICY_APPROVED")
    assert not tr.ok
    assert tr.status == "OWNER_POLICY_GATE"


def test_owner_policy_bound_token_required(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    for step in ("DISCOVER", "ASSESS", "POLICY_PROPOSED"):
        assert transition(root, step).ok
    policy = json.loads(
        (root / "opsx/engineering-adoption/policy-proposed.json").read_text(encoding="utf-8")
    )
    bad = f"{OWNER_APPROVE_PREFIX}{'0' * 64}"
    assert bad != f"{OWNER_APPROVE_PREFIX}{policy_proposal_content_sha256(policy)}"
    tr = transition(root, "OWNER_POLICY_GATE", owner_token=bad)
    assert not tr.ok


def test_verify_blocks_missing_pyright(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    (root / "opsx" / "project").mkdir(parents=True, exist_ok=True)
    import yaml

    caps = {"product": {"language": "python"}, "quality": {}}
    (root / "opsx/project/capabilities.yaml").write_text(
        yaml.safe_dump(caps, sort_keys=False),
        encoding="utf-8",
    )
    for step in ("DISCOVER", "ASSESS", "POLICY_PROPOSED"):
        assert transition(root, step).ok
    policy = json.loads(
        (root / "opsx/engineering-adoption/policy-proposed.json").read_text(encoding="utf-8")
    )
    token = f"{OWNER_APPROVE_PREFIX}{policy_proposal_content_sha256(policy)}"
    assert transition(root, "OWNER_POLICY_GATE", owner_token=token).ok
    with mock.patch("engineering_adoption._run_bounded") as bounded:
        bounded.return_value = {
            "returncode": 127,
            "timed_out": False,
            "stdout_tail": "",
            "stderr_tail": "missing",
        }
        tr = transition(root, "CONFIGURE")
        assert not tr.ok
        assert "pyright" in tr.detail.lower()
    assert transition(root, "STATUS").status == "CONFIGURE"
