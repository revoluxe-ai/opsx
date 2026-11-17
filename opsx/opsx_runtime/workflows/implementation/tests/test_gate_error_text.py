"""Tautological gate codes must name the missing field for any change."""

from __future__ import annotations

from agents.implementation.apply_design.read_source import (
    ReadSourceFailure,
    ReadSourceFailureCode,
    failure_payload,
)
from workflows.implementation.gate_error_text import format_gate_error_line
from workflows.implementation.routing_contract import implementation_lane_error_detail


def test_invalid_args_tautology_names_submit_and_read_fields() -> None:
    state = {
        "error": "INVALID_ARGS: INVALID_ARGS",
        "implementation_machine_gate_errors": [
            {"code": "INVALID_ARGS", "message": "INVALID_ARGS"},
        ],
    }
    text = implementation_lane_error_detail(state)
    assert text != "INVALID_ARGS: INVALID_ARGS"
    assert "content" in text
    assert "base_digest" in text
    assert "source_id" in text
    assert "expected_revision" in text

    from_gate = implementation_lane_error_detail(
        {
            "implementation_machine_gate_errors": [
                {
                    "code": "INVALID_ARGS",
                    "message": "INVALID_ARGS",
                    "field": "content",
                },
            ],
        },
    )
    assert from_gate.startswith("INVALID_ARGS:")
    assert "field=content" in from_gate
    assert "INVALID_ARGS: INVALID_ARGS" not in from_gate

    distinct = implementation_lane_error_detail(
        {
            "implementation_machine_gate_errors": [
                {"code": "SCHEMA", "message": "SCAFFOLD.json is not a scaffold receipt"},
            ],
        },
    )
    assert distinct == "SCHEMA: SCAFFOLD.json is not a scaffold receipt"


def test_prompt_line_and_read_source_do_not_repeat_invalid_args_code() -> None:
    line = format_gate_error_line({"code": "INVALID_ARGS", "message": "INVALID_ARGS"})
    assert line != "INVALID_ARGS: INVALID_ARGS"
    assert "base_digest" in line
    assert "content" in line

    empty = failure_payload(
        ReadSourceFailure(ReadSourceFailureCode.INVALID_ARGS, field="source_id")
    )
    assert empty["code"] == "INVALID_ARGS"
    assert empty["field"] == "source_id"
    assert "source_id" in empty["message"]
    assert empty["message"] != "INVALID_ARGS"
