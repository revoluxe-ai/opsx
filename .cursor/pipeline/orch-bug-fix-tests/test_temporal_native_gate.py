"""Adapter gate: Temporal repair hop must use native_mission."""

from __future__ import annotations

import importlib.util
from pathlib import Path

HELPER = Path(__file__).resolve().parents[1] / "orch-bug-fix" / "orch_bug_fix.py"


def _load():
    spec = importlib.util.spec_from_file_location("orch_bug_fix_gate", HELPER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _init(root: Path) -> None:
    opsx = root / "opsx"
    opsx.mkdir(parents=True)
    (opsx / "projectization.yaml").write_text(
        "status: COMPLETE\nnormal_commands_enabled: true\n", encoding="utf-8"
    )


def test_temporal_hop_rejects_legacy_adapter(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _init(root)
    adoption = Path(__file__).resolve().parents[1] / "engineering-adoption-tests" / "conftest.py"
    import importlib.util

    spec = importlib.util.spec_from_file_location("adopt_gate", adoption)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.write_verified_adoption(root)
    result = _load().run(
        root,
        {
            "temporal_repair_hop": "YES",
            "change": "c",
            "findings": [{"location": "app/a.py:1", "finding": "F1"}],
        },
    )
    assert result["REASON"] == "LEGACY_ORCH_ADAPTER_FORBIDDEN_USE_NATIVE_MISSION"
