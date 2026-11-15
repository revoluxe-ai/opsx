"""RED applicability: structural pack + вердикт семантики (без AST setenv duel)."""

from __future__ import annotations

from opsx.project_context import resource_path

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from workflows.repair.schemas import RedEvidence

RedSemanticsVerdictKind = Literal["APPLICABLE", "ASSERTION_MISMATCH", "INCONCLUSIVE"]

_IN_TEST_ENV_PATCH = re.compile(
    r"monkeypatch\.setenv|os\.environ\[(?:'|\")(?:INVEST_YALTA_PREVIEW|PREVIEW)",
    re.IGNORECASE,
)


class RedApplicabilityPack(BaseModel):
    """Детерминированный пакет для structural gate и read-only AI verifier."""

    model_config = ConfigDict(extra="forbid")

    requirement_fingerprint: str = ""
    obligation_harness_profile: str = "production_like"
    test_source_sha256: str | None = None
    test_node_id: str = ""
    pytest_exit_code: int | None = None
    red_effective_conditions: dict[str, Any] = Field(default_factory=dict)
    structural_links_ok: bool = True
    structural_errors: list[str] = Field(default_factory=list)


class RedSemanticsVerdict(BaseModel):
    """Структурированный вердикт applicability (DiagnosticAgent или offline stub)."""

    model_config = ConfigDict(extra="forbid")

    verdict: RedSemanticsVerdictKind
    evidence: list[str] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RedApplicabilityResult:
    """Итог двух слоёв applicability."""

    ok: bool
    stop_kind: str | None
    errors: list[str]
    pack: RedApplicabilityPack
    semantics: RedSemanticsVerdict | None = None


def _session_harness_profile() -> str:
    if os.environ.get("INVEST_YALTA_PREVIEW_UX_UNBLOCK") == "1":
        return "preview"
    return "production_like"


def _in_test_preview_override(test_path: Path) -> bool:
    try:
        text = test_path.read_text(encoding="utf-8")
    except OSError:
        return False
    return _IN_TEST_ENV_PATCH.search(text) is not None


def build_red_applicability_pack(
    *,
    evidence: RedEvidence,
    repo_root: Path | str,
    test_content_identity: str | None,
    pytest_result: dict[str, Any] | None,
    requirement_fingerprint: str = "",
    obligation_harness_profile: str = "production_like",
) -> RedApplicabilityPack:
    """Собрать RedApplicabilityPack после operative pytest."""
    root = Path(repo_root)
    node_id = str(evidence.test_id or "").strip()
    file_part = node_id.split("::", 1)[0] if node_id else ""
    test_path = (resource_path(root, file_part)).resolve() if file_part else None

    effective: dict[str, Any] = {
        "session_harness_profile": _session_harness_profile(),
        "in_test_preview_override": False,
    }
    if test_path is not None and test_path.is_file():
        effective["in_test_preview_override"] = _in_test_preview_override(test_path)

    structural_errors: list[str] = []
    if obligation_harness_profile == "production_like" and effective.get(
        "in_test_preview_override",
    ):
        structural_errors.append("RED_OPERATIVE_EFFECTIVE_PROFILE_MISMATCH")

    if not requirement_fingerprint.strip():
        structural_errors.append("RED_APPLICABILITY_MISSING_REQUIREMENT_FINGERPRINT")

    exit_code = None
    if pytest_result is not None:
        exit_code = int(pytest_result.get("exit_code", -1))

    return RedApplicabilityPack(
        requirement_fingerprint=requirement_fingerprint,
        obligation_harness_profile=obligation_harness_profile,
        test_source_sha256=test_content_identity,
        test_node_id=node_id,
        pytest_exit_code=exit_code,
        red_effective_conditions=effective,
        structural_links_ok=not structural_errors,
        structural_errors=structural_errors,
    )


def evaluate_red_applicability_structural(pack: RedApplicabilityPack) -> RedApplicabilityResult:
    """Только runtime structural gates (без AI)."""
    if pack.structural_errors:
        return RedApplicabilityResult(
            ok=False,
            stop_kind="TEST_SETUP_DEFECT",
            errors=list(pack.structural_errors),
            pack=pack,
        )
    return RedApplicabilityResult(
        ok=True,
        stop_kind=None,
        errors=[],
        pack=pack,
    )


def default_semantics_verdict(pack: RedApplicabilityPack) -> RedSemanticsVerdict:
    """Консервативный вердикт без LLM: structural pass → APPLICABLE."""
    _ = pack
    return RedSemanticsVerdict(verdict="APPLICABLE", evidence=["structural_gates_passed"])


def evaluate_red_applicability(
    pack: RedApplicabilityPack,
    *,
    semantics: RedSemanticsVerdict | None = None,
) -> RedApplicabilityResult:
    """Structural + optional semantics verdict."""
    base = evaluate_red_applicability_structural(pack)
    if not base.ok:
        return base
    verdict = semantics or default_semantics_verdict(pack)
    if verdict.verdict == "INCONCLUSIVE":
        return RedApplicabilityResult(
            ok=False,
            stop_kind="INCONCLUSIVE",
            errors=["RED_APPLICABILITY_INCONCLUSIVE"],
            pack=pack,
            semantics=verdict,
        )
    if verdict.verdict == "ASSERTION_MISMATCH":
        return RedApplicabilityResult(
            ok=False,
            stop_kind="TEST_SETUP_DEFECT",
            errors=["RED_APPLICABILITY_ASSERTION_MISMATCH", *verdict.evidence],
            pack=pack,
            semantics=verdict,
        )
    return RedApplicabilityResult(
        ok=True,
        stop_kind=None,
        errors=[],
        pack=pack,
        semantics=verdict,
    )


def pack_digest(pack: RedApplicabilityPack) -> str:
    """Stable digest for repair_attempts journal."""
    payload = pack.model_dump(mode="json")
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
