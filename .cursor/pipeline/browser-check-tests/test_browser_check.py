"""Fail-closed tests for target-native browser-check."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

HELPER = Path(__file__).resolve().parents[1] / "browser-check" / "browser_check.py"
HEAD = "a" * 40


def _load():
    spec = importlib.util.spec_from_file_location("browser_check", HELPER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _init(root: Path) -> None:
    opsx = root / "opsx"
    adapter = opsx / "adapters" / "poetry-python"
    adapter.mkdir(parents=True)
    (opsx / "projectization.yaml").write_text(
        "status: COMPLETE\nnormal_commands_enabled: true\n", encoding="utf-8"
    )
    (adapter / "README.md").write_text("# poetry-python\n", encoding="utf-8")


def _run(root: Path, **request: object) -> dict[str, object]:
    module = _load()
    return module.run(root, request, head_reader=lambda _path: HEAD)


def test_telegram_change_is_not_applicable_not_pass(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _init(root)
    change = root / "openspec" / "changes" / "bot-guard"
    change.mkdir(parents=True)
    (change / "specs" / "spec.md").parent.mkdir(parents=True)
    (change / "specs" / "spec.md").write_text(
        "#### Scenario: Telegram callback rejects missing actor\n",
        encoding="utf-8",
    )
    result = _run(root, change="bot-guard")
    assert result["STATUS"] == "NOT_APPLICABLE"
    assert result["PASS"] == "NO"
    assert result["EVIDENCE"]["scenario_heading_count"] == 1
    assert "telegram" in result["EVIDENCE"]["telegram_markers"]


def test_browser_scenario_without_adapter_blocks(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _init(root)
    change = root / "openspec" / "changes" / "web-ui"
    (change / "specs").mkdir(parents=True)
    (change / "specs" / "spec.md").write_text(
        "#### Scenario: Vue SPA home loads in browser\n",
        encoding="utf-8",
    )
    result = _run(root, change="web-ui")
    assert result["STATUS"] == "BLOCKED"
    assert result["REASON"] == "BROWSER_SURFACE_UNDECLARED"
    assert result["PASS"] == "NO"


def test_stale_revision_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _init(root)
    change = root / "openspec" / "changes" / "bot-guard"
    change.mkdir(parents=True)
    result = _run(root, change="bot-guard", revision="b" * 40)
    assert result["STATUS"] == "BLOCKED"
    assert result["REASON"] == "stale_revision"


def test_bounded_mode_is_binding_gap(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _init(root)
    result = _run(root, mode="bounded")
    assert result["STATUS"] == "BLOCKED"
    assert result["REASON"] == "BINDING_GAP"


def test_path_escape_blocked(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _init(root)
    result = _run(root, change="../outside")
    assert result["STATUS"] == "BLOCKED"


def test_cli_roundtrip(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    _init(root)
    change = root / "openspec" / "changes" / "bot-guard"
    change.mkdir(parents=True)
    (change / "proposal.md").write_text("telegram bot\n", encoding="utf-8")
    result = _run(root, change="bot-guard", revision=HEAD)
    assert result["CONTRACT_ID"] == "OPSX-WB-BROWSER-CHECK-01"
    dumped = json.dumps(result)
    assert "PASS" in dumped
