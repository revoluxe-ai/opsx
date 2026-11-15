"""Тесты discoverability CLASSIFIER (§21.3)."""

from __future__ import annotations

from pathlib import Path

from agents.budget.profiles import (
    _load_catalog,
    profile_id_for_role,
    resolve_profile_for_role,
)
from agents.repair.classifier_discoverability import (
    build_classifier_discoverability,
    enrich_classifier_payload,
)
from agents.repair.prompt_wire import build_role_prompt
from workflows.repair.schemas import RepairState


def test_build_classifier_discoverability_lists_migrations(tmp_path: Path) -> None:
    versions = tmp_path / "migrations" / "versions"
    versions.mkdir(parents=True)
    (versions / "0016_merge.py").write_text("rev", encoding="utf-8")
    (versions / "0017_tail.py").write_text("rev", encoding="utf-8")

    disc = build_classifier_discoverability(
        tmp_path,
        finding={
            "finding": "Сломана последняя миграция",
            "location": "migrations",
            "evidence_paths": [],
        },
    )
    assert disc["migration_focus"] is True
    assert "migrations/versions/0017_tail.py" in disc["source_ids"]
    assert "migrations/versions/0016_merge.py" in disc["source_ids"]
    assert disc["priority_reads"][0].startswith("migrations/versions/")


def test_enrich_classifier_payload_in_prompt_json() -> None:
    repo = Path(__file__).resolve().parents[5]
    state = RepairState.model_validate(
        {
            "mission_id": "m1",
            "repository_path": str(repo),
            "workspace": str(repo),
            "output_dir": str(repo / "opsx" / "runs" / "test-disc"),
            "revision": 0,
        }
    )
    payload = enrich_classifier_payload(
        state,
        {"finding": {"finding": "migration broken", "evidence_paths": []}},
    )
    assert "discoverability" in payload
    prompt = build_role_prompt(
        state,
        role="CLASSIFIER",
        role_instructions="classify",
        payload_model=RepairState,
        context=payload,
    )
    assert "discoverability" in prompt
    assert "migrations/versions" in prompt


def test_classifier_profile_has_higher_tool_cap() -> None:
    _load_catalog.cache_clear()
    assert profile_id_for_role("CLASSIFIER") == "classifier_discover"
    profile = resolve_profile_for_role("CLASSIFIER")
    assert profile.max_tool_invocations_per_hop >= 80