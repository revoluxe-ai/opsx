"""Параметризованная матрица извлечения JSON из ответов SDK-агентов."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from agents.utils.response import (
    extract_agent_json_text,
    is_agent_json_object,
    parse_agent_json,
)


class _ExtractProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: str
    score: int
    comment: str | None = None


class _RowReview(BaseModel):
    scenario_id: str
    assessment: str
    evidence: str = ""


class _NestedProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: str
    score: int = 0
    row_reviews: list[_RowReview] = []


@dataclass(frozen=True)
class ExtractCase:
    case_id: str
    raw: str
    expect: Literal["ok", "reject"]
    expected_verdict: str | None = None
    expected_score: int | None = None
    use_nested: bool = False


def _ok(verdict: str = "ok", score: int = 1, **extra: object) -> str:
    payload: dict[str, object] = {"verdict": verdict, "score": score}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def _build_extract_cases() -> list[ExtractCase]:
    ok_json = _ok()
    cases: list[ExtractCase] = [
        ExtractCase("bare_minimal", ok_json, "ok", "ok", 1),
        ExtractCase(
            "bare_pretty_newlines",
            '{\n  "verdict": "ok",\n  "score": 2\n}',
            "ok",
            "ok",
            2,
        ),
        ExtractCase(
            "fence_json_from_start",
            f"```json\n{ok_json}\n```",
            "ok",
            "ok",
            1,
        ),
        ExtractCase(
            "fence_generic_from_start",
            f"```\n{ok_json}\n```",
            "ok",
            "ok",
            1,
        ),
        ExtractCase(
            "prose_then_fence",
            f"Please see:\n```json\n{ok_json}\n```",
            "ok",
            "ok",
            1,
        ),
        ExtractCase(
            "prose_then_bare_json",
            "Structural audit: mapped test aligns.\n\n" + ok_json,
            "ok",
            "ok",
            1,
        ),
        ExtractCase(
            "cjk_prefix_then_json",
            f"勇气\n\n{ok_json}",
            "ok",
            "ok",
            1,
        ),
        ExtractCase(
            "emoji_prefix",
            f"✅ done\n\n{ok_json}",
            "ok",
            "ok",
            1,
        ),
        ExtractCase(
            "leading_whitespace",
            f"\n\n  \n{ok_json}",
            "ok",
            "ok",
            1,
        ),
        ExtractCase(
            "trailing_prose_after_json",
            ok_json + "\n\nThanks for reading.",
            "ok",
            "ok",
            1,
        ),
        ExtractCase(
            "two_objects_last_wins",
            '{"verdict":"stale","score":0}\n' + ok_json,
            "ok",
            "ok",
            1,
        ),
        ExtractCase(
            "brace_in_evidence_string",
            _ok(comment="line 137} and {brace in prose"),
            "ok",
            "ok",
            1,
        ),
        ExtractCase(
            "escaped_quotes_in_string",
            _ok(comment='said \\"hello\\"'),
            "ok",
            "ok",
            1,
        ),
        ExtractCase(
            "nested_row_reviews",
            json.dumps(
                {
                    "verdict": "ok",
                    "score": 3,
                    "row_reviews": [
                        {
                            "scenario_id": "spec::when",
                            "assessment": "SUPPORTED",
                            "evidence": "line 135",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            "ok",
            "ok",
            3,
            use_nested=True,
        ),
        ExtractCase(
            "unicode_cyrillic_values",
            _ok(comment="проверка кириллицы"),
            "ok",
            "ok",
            1,
        ),
        ExtractCase(
            "unicode_escape_in_json",
            '{"verdict":"ok","score":1,"comment":"\\u0410"}',
            "ok",
            "ok",
            1,
        ),
        ExtractCase(
            "large_single_line",
            _ok(comment="x" * 2000),
            "ok",
            "ok",
            1,
        ),
        ExtractCase(
            "tab_between_prose_and_json",
            f"Audit complete.\t{ok_json}",
            "ok",
            "ok",
            1,
        ),
        ExtractCase(
            "markdown_bold_in_prose_only",
            f"**PASS** structural audit\n\n{ok_json}",
            "ok",
            "ok",
            1,
        ),
        ExtractCase(
            "last_fence_among_many",
            "ignore\n```json\n[1]\n```\n"
            f"final\n```json\n{ok_json}\n```",
            "ok",
            "ok",
            1,
        ),
        ExtractCase(
            "prose_code_snippet_with_braces",
            "snippet: def f(): return {1: 2}\n\n" + ok_json,
            "ok",
            "ok",
            1,
        ),
        ExtractCase(
            "json_with_url_and_braces_in_string",
            _ok(comment="path/to/{id}/file"),
            "ok",
            "ok",
            1,
        ),
        ExtractCase(
            "scenario_incident_replay",
            "Structural audit: test maps to frozen row.\n\n"
            + json.dumps(
                {
                    "verdict": "SCENARIO_TEST_COVERAGE_PASS",
                    "score": 1,
                    "comment": "when_proofs [135]; outcome [137, 146]",
                },
                ensure_ascii=False,
            ),
            "ok",
            "SCENARIO_TEST_COVERAGE_PASS",
            1,
        ),
        ExtractCase(
            "local_hop_copy_block_newlines",
            json.dumps(
                {
                    "verdict": "ok",
                    "score": 1,
                    "comment": "Workspace: /r\nChange: x\nVerdict: ok",
                },
                ensure_ascii=False,
            ),
            "ok",
            "ok",
            1,
        ),
        ExtractCase(
            "nested_with_brace_in_evidence",
            json.dumps(
                {
                    "verdict": "ok",
                    "score": 4,
                    "row_reviews": [
                        {
                            "scenario_id": "s::x",
                            "assessment": "SUPPORTED",
                            "evidence": "THEN line 137}; AND {not a brace",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            "ok",
            "ok",
            4,
            use_nested=True,
        ),
        ExtractCase("fence_with_surrounding_prose", f"Note:\n```json\n{ok_json}\n```\n", "ok", "ok", 1),
    ]

    rejects: list[ExtractCase] = [
        ExtractCase("empty", "", "reject"),
        ExtractCase("whitespace_only", " \n\t ", "reject"),
        ExtractCase("prose_impl_ok_marker", "✅ IMPL_OK", "reject"),
        ExtractCase("prose_verdict_substring", "Verdict IMPL_OK after reading receipt", "reject"),
        ExtractCase("unterminated_fence", '```json\n{"verdict":"ok","score":', "reject"),
        ExtractCase("fence_without_json_object", "```json\n[1,2]\n```", "reject"),
        ExtractCase("root_json_array", '[{"verdict":"ok","score":1}]', "reject"),
        ExtractCase("root_json_scalar_string", '"hello"', "reject"),
        ExtractCase("root_json_scalar_number", "42", "reject"),
        ExtractCase("root_json_scalar_bool", "true", "reject"),
        ExtractCase("truncated_mid_object", '{"verdict":"ok","score":', "reject"),
        ExtractCase("truncated_mid_string", '{"verdict":"ok', "reject"),
        ExtractCase("only_open_brace", "not json {", "reject"),
        ExtractCase("prose_with_fake_braces", "{ not: valid, json }", "reject"),
        ExtractCase("two_truncated_objects", '{"a":1\n{"b":2', "reject"),
        ExtractCase("valid_array_inside_prose_only", "See array [1,2] in text", "reject"),
        ExtractCase("json_with_trailing_comma", '{"verdict":"ok","score":1,}', "reject"),
        ExtractCase("only_closing_brace", "}", "reject"),
        ExtractCase("mismatched_brackets", "{]", "reject"),
        ExtractCase("fence_json_plus_nested_unclosed", '```json\n{"verdict":"ok"\n```', "reject"),
        ExtractCase("bare_invalid_key", "{bad:1}", "reject"),
        ExtractCase("prose_only_curly", "Use {curly} braces in English", "reject"),
        ExtractCase("null_bytes_outside", "\x00garbage without json", "reject"),
        ExtractCase("incomplete_fence_prose", "Start\n```json\n", "reject"),
        ExtractCase("double_fence_both_invalid", "```json\n1\n```\n```json\n2\n```", "reject"),
    ]
    return cases + rejects


EXTRACT_CASES = _build_extract_cases()
OK_CASES = [c for c in EXTRACT_CASES if c.expect == "ok"]
REJECT_CASES = [c for c in EXTRACT_CASES if c.expect == "reject"]


@pytest.mark.parametrize("case", EXTRACT_CASES, ids=lambda c: c.case_id)
def test_extract_and_parse_agent_json(case: ExtractCase) -> None:
    if case.expect == "reject":
        with pytest.raises((ValidationError, ValueError)):
            parse_agent_json(
                case.raw,
                _NestedProbe if case.use_nested else _ExtractProbe,
            )
        return

    model_type = _NestedProbe if case.use_nested else _ExtractProbe
    parsed = parse_agent_json(case.raw, model_type)
    assert parsed.verdict == case.expected_verdict
    if case.expected_score is not None and not case.use_nested:
        assert parsed.score == case.expected_score
    if case.use_nested and case.expected_score is not None:
        assert parsed.score == case.expected_score


@pytest.mark.parametrize("case", REJECT_CASES, ids=lambda c: c.case_id)
def test_extract_agent_json_text_rejects(case: ExtractCase) -> None:
    with pytest.raises(ValueError, match="Agent JSON object not found|Unterminated"):
        extract_agent_json_text(case.raw)


@pytest.mark.parametrize("case", OK_CASES, ids=lambda c: c.case_id)
def test_is_agent_json_object_matches_parse(case: ExtractCase) -> None:
    assert is_agent_json_object(case.raw)


@pytest.mark.parametrize("case", OK_CASES, ids=lambda c: c.case_id)
def test_extracted_bytes_stable(case: ExtractCase) -> None:
    first = extract_agent_json_text(case.raw)
    second = extract_agent_json_text(case.raw)
    assert first == second
    assert json.loads(first)


def test_extract_case_count_meets_plan_minimum() -> None:
    assert len(EXTRACT_CASES) >= 45
    assert len(OK_CASES) >= 25
    assert len(REJECT_CASES) >= 20


def test_incident_05760199_prose_prefix_parses() -> None:
    """Регрессия: prose / CJK prefix не должны вызывать VALIDATION_NO_PROGRESS на JSON."""
    raw = (
        "Structural audit: aligned WHEN/THEN.\n\n"
        '{"verdict":"SCENARIO_TEST_COVERAGE_PASS","score":1}'
    )
    parsed = parse_agent_json(raw, _ExtractProbe)
    assert parsed.verdict == "SCENARIO_TEST_COVERAGE_PASS"
