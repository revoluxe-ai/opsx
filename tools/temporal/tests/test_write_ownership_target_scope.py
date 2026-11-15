"""Граница записи Bugbot для stroy-system: реальный evaluate_write_ownership.

Тесты поднимают временный git-репозиторий и вызывают guard напрямую.
Подмена вердикта не используется.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from target_capabilities import authorized_repair_write_prefixes
from write_ownership import (
    capture_worktree,
    evaluate_write_ownership,
    is_runtime_artifact,
)

_STROY_PREFIXES = ("src/stroy", "tests/unit", "tests/integration")


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=root,
        check=True,
        capture_output=True,
    )


def _write(path: Path, text: str = "x\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_live_capabilities_resolve_stroy_roots(monkeypatch) -> None:
    """Боевой capabilities.json, без тестового override app/tests."""

    monkeypatch.delenv("OPSX_TEMPORAL_TEST_SOURCE_ROOTS", raising=False)
    monkeypatch.delenv("OPSX_TEMPORAL_TEST_TEST_ROOTS", raising=False)
    prefixes = authorized_repair_write_prefixes()
    assert prefixes == ("src/stroy/", "tests/unit/", "tests/integration/")
    assert "app/" not in prefixes
    assert "*" not in prefixes


def test_repair_write_prefixes_fail_closed_when_roots_missing(monkeypatch) -> None:
    monkeypatch.setenv("OPSX_TEMPORAL_TEST_SOURCE_ROOTS", " ")
    monkeypatch.setenv("OPSX_TEMPORAL_TEST_TEST_ROOTS", " ")
    try:
        authorized_repair_write_prefixes()
    except RuntimeError as exc:
        assert "TARGET_CAPABILITY_UNRESOLVED" in str(exc)
    else:
        raise AssertionError("empty source and test roots must fail closed")


def test_stroy_product_and_test_repair_with_runtime_artifacts(tmp_path: Path) -> None:
    """Служебные ledger/config не блокируют правку src/stroy и tests/unit."""

    _git_init(tmp_path)
    _write(tmp_path / "opsx/cursor-executor-ledger/exec/record.json", "{}\n")
    _write(tmp_path / "opsx/runs/temporal/config/agent-models.overlay.json", "{}\n")
    _write(tmp_path / "opsx/runs/temporal/runs/bugbot-repair-poc-real.json", "{}\n")
    _write(tmp_path / ".tmp/temporal/git-preflight-latest.md", "preflight\n")
    baseline = capture_worktree(tmp_path)
    _write(tmp_path / "src/stroy/channels/max/invest_yalta/schedule_actions.py", "def f() -> None:\n    return None\n")
    _write(tmp_path / "tests/unit/channels/max/test_schedule.py", "def test_ok() -> None:\n    assert True\n")
    verdict = evaluate_write_ownership(
        tmp_path,
        baseline=baseline,
        authorized_prefixes=_STROY_PREFIXES,
    )
    assert verdict.blocked is False
    assert verdict.terminal is None


def test_foreign_product_and_process_paths_are_blocked(tmp_path: Path) -> None:
    _git_init(tmp_path)
    baseline = capture_worktree(tmp_path)
    _write(tmp_path / "app/app.js", "donor\n")
    _write(tmp_path / "tests-evil/test_x.py", "x\n")
    _write(tmp_path / "tools/temporal/write_ownership.py", "x\n")
    _write(tmp_path / "opsx/project/capabilities.yaml", "x\n")
    verdict = evaluate_write_ownership(
        tmp_path,
        baseline=baseline,
        authorized_prefixes=_STROY_PREFIXES,
    )
    assert verdict.blocked is True
    assert verdict.terminal == "BLOCKED_WRITE_OWNERSHIP"
    reason = verdict.reason
    assert "app/app.js" in reason
    assert "tests-evil/test_x.py" in reason
    assert "tools/temporal/write_ownership.py" in reason
    assert "opsx/project/capabilities.yaml" in reason


def test_prefix_match_rejects_traversal_and_absolute_paths() -> None:
    """``..`` и абсолютный путь не входят в scope и не становятся scratch."""

    from write_ownership import _in_scope

    assert _in_scope("tools/temporal/write_ownership.py", _STROY_PREFIXES) is False
    assert _in_scope("/etc/passwd", _STROY_PREFIXES) is False
    assert _in_scope("src/stroy/../../tools/temporal/write_ownership.py", _STROY_PREFIXES) is False
    assert _in_scope("src/stroy/../../tools/temporal/write_ownership.py", ("*",)) is False
    assert _in_scope("src/stroy/handler.py", _STROY_PREFIXES) is True
    assert is_runtime_artifact("opsx/cursor-executor-ledger/exec/record.json") is True
    assert is_runtime_artifact("opsx/project/capabilities.yaml") is False
    assert is_runtime_artifact("opsx/cursor-executor-ledger/../../secrets.txt") is False


def test_symlink_and_scratch_symlink_cannot_escape(tmp_path: Path) -> None:
    """Symlink внутри разрешённого префикса или scratch не открывает чужой файл."""

    _git_init(tmp_path)
    outside = tmp_path / "tools" / "secret.py"
    _write(outside, "SECRET\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "outside"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    baseline = capture_worktree(tmp_path)
    leak = tmp_path / "src/stroy/leak.py"
    leak.parent.mkdir(parents=True, exist_ok=True)
    leak.symlink_to(outside)
    verdict = evaluate_write_ownership(
        tmp_path,
        baseline=baseline,
        authorized_prefixes=_STROY_PREFIXES,
    )
    assert verdict.blocked is True
    assert "src/stroy/leak.py" in verdict.reason
    leak.unlink()

    scratch = tmp_path / "opsx/cursor-executor-ledger/esc"
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.symlink_to(outside)
    verdict = evaluate_write_ownership(
        tmp_path,
        baseline=baseline,
        authorized_prefixes=_STROY_PREFIXES,
    )
    assert verdict.blocked is True
    assert "opsx/cursor-executor-ledger/esc" in verdict.reason
    scratch.unlink()

    inside = tmp_path / "src/stroy/kept.py"
    _write(inside, "kept\n")
    alias = tmp_path / "src/stroy/alias.py"
    alias.symlink_to(inside)
    verdict = evaluate_write_ownership(
        tmp_path,
        baseline=baseline,
        authorized_prefixes=_STROY_PREFIXES,
    )
    assert verdict.blocked is False

    alias.unlink()
    inside.unlink()
    outside_link = tmp_path / "src/stroy/abs.py"
    outside_link.symlink_to("/etc/passwd")
    verdict = evaluate_write_ownership(
        tmp_path,
        baseline=baseline,
        authorized_prefixes=("*",),
    )
    assert verdict.blocked is True
    assert "src/stroy/abs.py" in verdict.reason


def test_capability_wildcard_does_not_open_the_repository(monkeypatch) -> None:
    """Stage: голый ``*`` в корнях запрещён. Нормализатор превращает env ``*`` в ``*/``.

    ``*/`` не равен ``*`` и не делает ``_in_scope`` истинным для всего репозитория.
    """

    from write_ownership import _in_scope

    monkeypatch.setattr("target_capabilities.product_source_roots", lambda: ("*",))
    monkeypatch.setattr("target_capabilities.product_test_roots", lambda: ())
    try:
        authorized_repair_write_prefixes()
    except RuntimeError as exc:
        assert "wildcard" in str(exc)
    else:
        raise AssertionError("a literal * root must fail closed")

    monkeypatch.undo()
    monkeypatch.setenv("OPSX_TEMPORAL_TEST_SOURCE_ROOTS", "*")
    monkeypatch.setenv("OPSX_TEMPORAL_TEST_TEST_ROOTS", "tests/unit")
    prefixes = authorized_repair_write_prefixes()
    assert prefixes == ("*/", "tests/unit/")
    assert "*" not in prefixes
    assert _in_scope("src/stroy/handler.py", prefixes) is False
    assert _in_scope("tools/temporal/write_ownership.py", prefixes) is False
    assert _in_scope("tests/unit/test_x.py", prefixes) is True


def test_preexisting_owner_dirt_and_receipt_stay(tmp_path: Path) -> None:
    _git_init(tmp_path)
    receipt = tmp_path / "opsx/runs/bug-cases/materialize/PROVIDER-kept.md"
    _write(receipt, "RESULT: FAIL\n")
    _write(tmp_path / "owner.txt", "owner\n")
    baseline = capture_worktree(tmp_path)
    verdict = evaluate_write_ownership(
        tmp_path,
        baseline=baseline,
        authorized_prefixes=_STROY_PREFIXES,
    )
    assert verdict.blocked is True
    assert "owner.txt" in verdict.reason
    assert "PROVIDER-kept.md" in verdict.reason
    assert receipt.read_text(encoding="utf-8") == "RESULT: FAIL\n"
    assert (tmp_path / "owner.txt").read_text(encoding="utf-8") == "owner\n"


def test_new_materialize_receipt_is_not_foreign(tmp_path: Path) -> None:
    _git_init(tmp_path)
    baseline = capture_worktree(tmp_path)
    _write(tmp_path / "opsx/runs/bug-cases/materialize/PROVIDER-new.md", "RESULT: PASS\n")
    verdict = evaluate_write_ownership(
        tmp_path,
        baseline=baseline,
        authorized_prefixes=_STROY_PREFIXES,
    )
    assert verdict.blocked is False
