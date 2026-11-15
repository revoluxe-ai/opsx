"""Classifier HIGH labels override pytest repair_workflow."""

from agents.verify.group_error.schemas import Finding
from agents.verify.pytest_error_classifier.merge import apply_classifier_label_overrides


def test_syntax_error_labeled_collection() -> None:
    findings = [
        Finding(
            finding_id="F-0001",
            source_tool="pytest",
            repair_workflow="WF_PYTEST_FAILURE",
            category="TEST_FAILURE",
            observation="tests/foo.py:1: SyntaxError",
            classification="FACT",
            evidence_refs=["quality_gate_diagnostics.pytest"],
        ),
    ]
    state = {
        "pytest_error_classifier_result": {
            "status": "CLASSIFIED",
            "labels": [
                {
                    "finding_id": "F-0001",
                    "axis": "BUILD",
                    "confidence": "HIGH",
                    "rationale": "fast",
                },
            ],
        },
    }
    out = apply_classifier_label_overrides(findings, state)
    assert out[0].repair_workflow == "WF_PYTEST_COLLECTION"
