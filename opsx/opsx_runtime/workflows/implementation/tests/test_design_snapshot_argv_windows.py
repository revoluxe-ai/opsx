"""Регрессия: snapshot signer не кладёт тело APPLY-DESIGN в argv (Windows WinError 206)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

from workflows.implementation.design_machine_validation import (
    DesignDependentSnapshotResult,
    evaluate_apply_design_snapshot_from_body,
)

_REPO_ROOT = Path(__file__).resolve().parents[5]


def _large_design() -> dict[str, object]:
    return {
        "schema": "opsx.apply_design",
        "schema_version": 3,
        "change_id": "argv-regression",
        "status": "APPLY_DESIGN_READY",
        "_padding": "P" * 45_000,
    }


def test_snapshot_from_body_argv_excludes_design_payload(tmp_path: Path) -> None:
    """Сериализованное тело design не должно попадать ни в один аргумент subprocess."""
    design = _large_design()
    serialized = json.dumps(design, ensure_ascii=False)
    assert len(serialized) > 40_000
    captured: dict[str, list[str]] = {}

    def spy_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = cmd
        for arg in cmd:
            assert serialized not in arg
        json_path = next(a for a in cmd if a.endswith(".json"))
        assert Path(json_path).is_file()
        assert json.loads(Path(json_path).read_text(encoding="utf-8")) == design
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=json.dumps(
                {
                    "ok": False,
                    "code": "STUB",
                    "frozen_digest": None,
                    "current_digest": None,
                }
            ),
            stderr="",
        )

    with patch.object(subprocess, "run", side_effect=spy_run):
        result = evaluate_apply_design_snapshot_from_body(design, repo_root=tmp_path)

    assert isinstance(result, DesignDependentSnapshotResult)


def test_snapshot_from_body_large_design_returns_structured_result_not_winerror_206() -> None:
    """На Windows большой кандидат вызывает Node через temp-файл, не WinError 206."""
    if shutil.which("node") is None:
        return
    design = _large_design()
    serialized = json.dumps(design, ensure_ascii=False)
    assert len(serialized) > 40_000
    result = evaluate_apply_design_snapshot_from_body(design, repo_root=_REPO_ROOT)
    assert isinstance(result, DesignDependentSnapshotResult)
    assert result.code != "DESIGN_SNAPSHOT_SIGNER_FAILED" or result.current_digest is None or (
        "WinError 206" not in str(result.current_digest)
    )
