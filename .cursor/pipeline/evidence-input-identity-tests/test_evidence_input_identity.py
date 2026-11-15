"""Проверки input identity execution receipt.

Коммит receipt не меняет идентичность входов. Изменение любой материальной
роли делает предыдущий PASS stale.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "evidence-input-identity"
    / "evidence_input_identity.py"
)
ROLES = ("product", "tests", "specs", "validators", "configs")
LAYOUT = {role: [role] for role in ROLES}
RECEIPT_REL = "opsx/runs/scenario-coverage/demo/receipts/PROVIDER-abc.md"
NESTED_RECEIPT = "product/opsx/runs/scenario-coverage/demo/receipts/nested.md"


def _load() -> ModuleType:
    """Загрузить оракул по пути файла, без установки пакета."""

    spec = importlib.util.spec_from_file_location("evidence_input_identity", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ORACLE = _load()


def _git(repo: Path, *args: str) -> str:
    """Выполнить git в фикстурном репозитории и вернуть stdout."""

    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    """Собрать дерево пяти материальных ролей и первый коммит."""

    repo = tmp_path / "repo"
    bodies = {
        "product": "def lead():\n    return 1\n",
        "tests": "def test_lead():\n    assert True\n",
        "specs": "# spec\nThe form stores a lead.\n",
        "validators": "def validate():\n    return True\n",
        "configs": "feature: on\n",
    }
    for role, body in bodies.items():
        path = repo / role / "item.txt"
        path.parent.mkdir(parents=True)
        _ = path.write_text(body, encoding="utf-8")
    _ = subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    _ = subprocess.run(
        ["git", "config", "user.email", "identity@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    _ = subprocess.run(
        ["git", "config", "user.name", "Identity Test"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    _ = subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True, text=True)
    _ = subprocess.run(
        ["git", "commit", "-m", "material"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return repo


def _identity(repo: Path, head: str) -> object:
    """Посчитать identity, исключив канонический receipt."""

    return ORACLE.compute_input_identity(
        repo,
        LAYOUT,
        provenance_head=head,
        receipt_paths=(RECEIPT_REL, NESTED_RECEIPT),
    )


def test_adding_and_committing_receipt_does_not_change_input_identity(tmp_path: Path) -> None:
    """Добавление и коммит receipt сохраняют identity и свежесть PASS."""

    repo = _init_repo(tmp_path)
    head_before = _git(repo, "rev-parse", "HEAD")
    before = _identity(repo, head_before)
    other_head = _identity(repo, "a" * 40)
    assert other_head.digest == before.digest
    assert before.provenance_head == head_before

    canonical = repo / RECEIPT_REL
    canonical.parent.mkdir(parents=True)
    nested = repo / NESTED_RECEIPT
    nested.parent.mkdir(parents=True)
    _ = nested.write_text("receipt bytes inside a walked role\n", encoding="utf-8")
    added = _identity(repo, head_before)
    assert added.digest == before.digest
    assert NESTED_RECEIPT not in added.files["product"]
    assert ORACLE.is_receipt_artifact(RECEIPT_REL)
    assert ORACLE.is_receipt_artifact(NESTED_RECEIPT)

    receipt_text = ORACLE.format_pass_receipt(before)
    _ = canonical.write_text(receipt_text, encoding="utf-8")
    _ = subprocess.run(
        ["git", "add", RECEIPT_REL, NESTED_RECEIPT],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    staged = _identity(repo, head_before)
    assert staged.digest == before.digest
    _ = subprocess.run(
        ["git", "commit", "-m", "record provider receipt"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    head_after = _git(repo, "rev-parse", "HEAD")
    assert head_after != head_before
    after = _identity(repo, head_after)
    assert after.digest == before.digest

    verdict = ORACLE.assess_pass_freshness(
        receipt_text=receipt_text,
        root=repo,
        provenance_head=head_after,
        layout=LAYOUT,
    )
    assert verdict.status == "FRESH"
    assert verdict.fresh is True
    assert verdict.reason == "INPUT_IDENTITY_MATCH"
    assert verdict.bound_identity == before.digest
    assert verdict.current_identity == before.digest
    assert verdict.provenance_head_recorded == head_before
    assert verdict.provenance_head_current == head_after


@pytest.mark.parametrize("role", ROLES)
def test_material_input_change_stales_previous_pass(tmp_path: Path, role: str) -> None:
    """Изменение байт одной материальной роли делает предыдущий PASS stale."""

    repo = _init_repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    before = _identity(repo, head)
    receipt_text = ORACLE.format_pass_receipt(before)
    fresh = ORACLE.assess_pass_freshness(
        receipt_text=receipt_text,
        root=repo,
        provenance_head=head,
        layout=LAYOUT,
    )
    assert fresh.status == "FRESH"

    target = repo / role / "item.txt"
    _ = target.write_text(target.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
    verdict = ORACLE.assess_pass_freshness(
        receipt_text=receipt_text,
        root=repo,
        provenance_head=head,
        layout=LAYOUT,
    )
    assert verdict.status == "STALE"
    assert verdict.fresh is False
    assert verdict.reason == "MATERIAL_INPUT_CHANGED"
    assert verdict.changed_roles == (role,)
    assert verdict.current_identity != before.digest
    assert verdict.provenance_head_recorded == head


def test_receipt_listed_as_material_file_fails_closed(tmp_path: Path) -> None:
    """Явный файл receipt в материальной роли не даёт identity и не даёт fresh PASS."""

    repo = _init_repo(tmp_path)
    receipt = repo / RECEIPT_REL
    receipt.parent.mkdir(parents=True)
    _ = receipt.write_text("PROVIDER_EXECUTION_RECEIPT:\nRESULT: PASS\n", encoding="utf-8")
    layout = dict(LAYOUT)
    layout["product"] = ["product", RECEIPT_REL]
    with pytest.raises(ORACLE.InputIdentityError) as caught:
        _ = ORACLE.compute_input_identity(repo, layout, receipt_paths=(RECEIPT_REL,))
    assert caught.value.code == "RECEIPT_EXCLUDED"


def test_missing_role_is_not_a_fresh_pass(tmp_path: Path) -> None:
    """Пустая роль отклоняется и не выглядит как свежий PASS."""

    repo = _init_repo(tmp_path)
    layout = dict(LAYOUT)
    del layout["configs"]
    with pytest.raises(ORACLE.InputIdentityError) as caught:
        _ = ORACLE.compute_input_identity(repo, layout)
    assert caught.value.code == "MATERIAL_INPUT_MISSING"
    verdict = ORACLE.assess_pass_freshness(
        receipt_text="RESULT: PASS\n",
        root=repo,
        provenance_head="abc",
    )
    assert verdict.fresh is False
    assert verdict.status == "STALE"
    assert verdict.reason == "IDENTITY_ABSENT"


def test_require_oracle_rejects_validator_set_without_module(tmp_path: Path) -> None:
    """Сертификация без файла оракула в validators не получает identity."""

    repo = _init_repo(tmp_path)
    with pytest.raises(ORACLE.InputIdentityError) as caught:
        _ = ORACLE.compute_input_identity(repo, LAYOUT, require_oracle=True)
    assert caught.value.code == "ORACLE_NOT_IN_VALIDATORS"


def test_non_pass_result_is_not_fresh(tmp_path: Path) -> None:
    """RESULT отличный от PASS не становится fresh при совпавшей identity."""

    repo = _init_repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    identity = _identity(repo, head)
    receipt_text = ORACLE.format_pass_receipt(identity, result="FAIL")
    verdict = ORACLE.assess_pass_freshness(
        receipt_text=receipt_text,
        root=repo,
        provenance_head=head,
    )
    assert verdict.fresh is False
    assert verdict.reason == "RESULT_NOT_PASS"


def test_indented_and_column_zero_receipts_stay_fresh(tmp_path: Path) -> None:
    """Схема с отступом и тот же текст с нулевой колонки оба остаются FRESH."""

    repo = _init_repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    identity = _identity(repo, head)
    indented = ORACLE.format_pass_receipt(identity)
    assert "\n  INPUT_ROOT product:" in indented
    assert "\n  ROLE_DIGEST tests:" in indented
    assert "\n  RECEIPT_PATH:" in indented
    column_zero = "\n".join(
        line[2:] if line.startswith("  ") else line for line in indented.splitlines()
    )
    for receipt_text in (indented, column_zero + "\n"):
        verdict = ORACLE.assess_pass_freshness(
            receipt_text=receipt_text,
            root=repo,
            provenance_head=head,
            layout=LAYOUT,
        )
        assert verdict.status == "FRESH"
        assert verdict.fresh is True


def test_material_file_cannot_be_hidden_as_receipt_path(tmp_path: Path) -> None:
    """Материальный путь в RECEIPT_PATH не выпадает из identity и не прячет правку."""

    repo = _init_repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    before = _identity(repo, head)
    with pytest.raises(ORACLE.InputIdentityError) as caught:
        _ = ORACLE.compute_input_identity(
            repo,
            LAYOUT,
            provenance_head=head,
            receipt_paths=("product/item.txt",),
        )
    assert caught.value.code == "RECEIPT_PATH_NOT_ARTIFACT"
    target = repo / "product" / "item.txt"
    _ = target.write_text(target.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
    verdict = ORACLE.assess_pass_freshness(
        receipt_text=ORACLE.format_pass_receipt(before),
        root=repo,
        provenance_head=head,
        layout=LAYOUT,
    )
    assert verdict.status == "STALE"
    assert verdict.fresh is False
    assert verdict.changed_roles == ("product",)


def test_cli_reports_fresh_then_stale(tmp_path: Path) -> None:
    """CLI печатает FRESH, затем STALE после изменения материального файла."""

    repo = _init_repo(tmp_path)
    head = _git(repo, "rev-parse", "HEAD")
    identity = _identity(repo, head)
    layout_path = repo / "layout.json"
    _ = layout_path.write_text(
        json.dumps({**LAYOUT, "receipt_paths": [RECEIPT_REL, NESTED_RECEIPT]}),
        encoding="utf-8",
    )
    receipt = repo / RECEIPT_REL
    receipt.parent.mkdir(parents=True)
    _ = receipt.write_text(ORACLE.format_pass_receipt(identity), encoding="utf-8")
    fresh = subprocess.run(
        [
            sys.executable,
            str(MODULE_PATH),
            "assess",
            "--root",
            str(repo),
            "--layout",
            str(layout_path),
            "--receipt",
            str(receipt),
            "--provenance-head",
            head,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert fresh.returncode == 0
    assert "STATUS: FRESH" in fresh.stdout
    target = repo / "configs" / "item.txt"
    _ = target.write_text(target.read_text(encoding="utf-8") + "x\n", encoding="utf-8")
    stale = subprocess.run(
        [
            sys.executable,
            str(MODULE_PATH),
            "assess",
            "--root",
            str(repo),
            "--layout",
            str(layout_path),
            "--receipt",
            str(receipt),
            "--provenance-head",
            head,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert stale.returncode == 2
    assert "STATUS: STALE" in stale.stdout
    assert "REASON: MATERIAL_INPUT_CHANGED" in stale.stdout
    assert "CHANGED_ROLES: configs" in stale.stdout
