"""TARGET capability write-scope resolution for Temporal repair gates."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from target_capabilities import (
    authorized_repair_write_prefixes,
    product_source_roots,
    product_test_roots,
)


def test_authorized_repair_write_prefixes_match_stroy_product_layout(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPSX_TEMPORAL_TEST_SOURCE_ROOTS", "src/stroy")
    monkeypatch.setenv("OPSX_TEMPORAL_TEST_TEST_ROOTS", "tests/unit;tests/integration")
    scopes = authorized_repair_write_prefixes()
    assert scopes == (
        "src/stroy/",
        "tests/unit/",
        "tests/integration/",
    )


def test_authorized_repair_write_prefixes_fail_closed_when_unresolved(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.delenv("OPSX_TEMPORAL_TEST_SOURCE_ROOTS", raising=False)
    monkeypatch.delenv("OPSX_TEMPORAL_TEST_TEST_ROOTS", raising=False)
    caps = tmp_path / "capabilities.json"
    caps.write_text(json.dumps({"product": {"source_roots": [], "test_roots": []}}), encoding="utf-8")
    monkeypatch.setattr("target_capabilities._CAPS", caps)
    with pytest.raises(RuntimeError, match="TARGET_CAPABILITY_UNRESOLVED"):
        authorized_repair_write_prefixes()


def test_repo_capabilities_json_declares_stroy_roots():
    """Документирует фактический TARGET в рабочей копии (не подменяет env-тесты)."""

    roots = product_source_roots() + product_test_roots()
    if os.environ.get("OPSX_TEMPORAL_TEST_SOURCE_ROOTS"):
        pytest.skip("conftest overrides capabilities for pytest")
    assert "src/stroy/" in roots
    assert "tests/unit/" in roots
