"""Fail-closed tests for post-deploy acceptance."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

HELPER = (
    Path(__file__).resolve().parents[1]
    / "post-deploy-acceptance"
    / "post_deploy_acceptance.py"
)
HEAD = "a" * 40


def _load():
    spec = importlib.util.spec_from_file_location("post_deploy_acceptance", HELPER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _init(root: Path) -> Path:
    opsx = root / "opsx"
    adapter = opsx / "adapters" / "poetry-python"
    adapter.mkdir(parents=True)
    (opsx / "projectization.yaml").write_text(
        "status: COMPLETE\nnormal_commands_enabled: true\n", encoding="utf-8"
    )
    receipt_dir = opsx / "runs" / "devops" / "20260930T000000Z"
    receipt_dir.mkdir(parents=True)
    receipt = receipt_dir / "DEPLOY_RESULT.md"
    receipt.write_text(
        "STATUS: SUCCESS\nEXECUTION_ID: exec-1\nPROBE: deploy/adv-bot readyReplicas>=1\n",
        encoding="utf-8",
    )
    return receipt


def _run(root: Path, **request: object) -> dict[str, object]:
    module = _load()
    return module.run(root, request, head_reader=lambda _path: HEAD)


def test_receipt_bound_pass(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    receipt = _init(root)
    rel = receipt.relative_to(root).as_posix()
    result = _run(root, deploy_receipt=rel, catalog="smoke", execution_id="exec-1")
    assert result["STATUS"] == "PASS"
    assert result["PRODUCTION_WRITES"] == "NO"
    assert "deploy/adv-bot readyReplicas>=1" in result["PROBES"]


def test_go_is_production_mutation_forbidden(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    receipt = _init(root)
    result = _run(
        root,
        deploy_receipt=receipt.relative_to(root).as_posix(),
        GO=True,
    )
    assert result["REASON"] == "PRODUCTION_MUTATION_FORBIDDEN"
    assert result["PASS"] == "NO"


def test_foreign_host_blocked(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    receipt = _init(root)
    result = _run(
        root,
        deploy_receipt=receipt.relative_to(root).as_posix(),
        base_url="https://tiriel.ru",
    )
    assert result["REASON"] == "URL_NOT_ALLOWLISTED"


def test_receipt_outside_devops_blocked(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _init(root)
    other = root / "tmp" / "DEPLOY_RESULT.md"
    other.parent.mkdir()
    other.write_text("STATUS: SUCCESS\nPROBE: x\n", encoding="utf-8")
    result = _run(root, deploy_receipt="tmp/DEPLOY_RESULT.md")
    assert result["REASON"] == "receipt_outside_devops_runs"


def test_stale_execution_blocked(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    receipt = _init(root)
    result = _run(
        root,
        deploy_receipt=receipt.relative_to(root).as_posix(),
        execution_id="other",
    )
    assert result["REASON"] == "stale_deploy_receipt"


def test_missing_receipt_blocks(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _init(root)
    result = _run(root, catalog="smoke")
    assert result["REASON"] == "deploy_receipt_required"
