"""Split-apply prompt'ы не читают ``.cursor/commands`` в runtime."""

from __future__ import annotations

import unittest
from pathlib import Path

from agents.implementation.apply_design.prompt.get_prompt import get_prompt_sync as design_prompt
from agents.implementation.apply_design_review.prompt.get_prompt import (
    get_prompt as design_review_prompt,
)


class SplitApplyPromptAuthorityTests(unittest.TestCase):
    def test_apply_design_prompt_includes_dispatched_slice(self) -> None:
        from agents.implementation.apply_red.prompt.get_prompt import get_prompt as red_prompt

        prompt = red_prompt(
            repository_path="C:/repo",
            change_id="visual",
            impl_verify_answer={"slice_id": "SLICE-001"},
            artifact_bundle={},
            machine_context={},
        )
        self.assertIn("Dispatched slice", prompt)
        self.assertIn("SLICE-001", prompt)
        self.assertIn("only routing signal", prompt)
        self.assertNotIn("Answer JSON schema:", prompt)
        self.assertNotIn("ApplyRedAnswer", prompt)

    def test_slice_receipt_gate_block_in_red_prompt(self) -> None:
        from agents.implementation.apply_red.prompt.get_prompt import get_prompt as red_prompt

        prompt = red_prompt(
            repository_path="C:/repo",
            change_id="visual",
            impl_verify_answer={
                "slice_id": "SLICE-001",
                "slice_receipt_validation": {
                    "valid": False,
                    "code": "SCOPE_NOT_ESTABLISHED",
                    "work_paths": ["tests/x.py"],
                    "stage": "APPLY_RED",
                    "slice_id": "SLICE-001",
                    "gate": "PRE_AGENT",
                },
            },
            artifact_bundle={},
            machine_context={},
        )
        self.assertIn("Deterministic slice receipt gate", prompt)
        self.assertIn("do NOT re-validate or write receipts", prompt)
        self.assertIn("Gate FAIL", prompt)
        self.assertNotIn("LangGraph calls the plan node", prompt)
        self.assertIn("scaffold → red → implement → green", prompt)
        self.assertIn("Slice receipt gate (LangGraph)", prompt)

    def test_scaffold_prompt_escalates_design_gap(self) -> None:
        from agents.config import check_paths_in_scope, resolve_read_scope
        from agents.implementation.apply_scaffold.prompt.get_prompt import (
            get_prompt as scaffold_prompt,
        )

        prompt = scaffold_prompt(
            repository_path="C:/repo",
            change_id="visual",
            impl_verify_answer={"slice_id": "SLICE-001"},
            artifact_bundle={},
            machine_context={},
        )
        self.assertIn("Mid-slice hop", prompt)
        self.assertIn("ESCALATE_ARBITER", prompt)
        self.assertNotIn("MUST NOT return to APPLY DESIGN", prompt)
        self.assertIn("Read scope (hard)", prompt)
        self.assertIn("openspec/schemas/**", prompt)
        self.assertIn("entire `opsx/**` tree", prompt)
        self.assertIn("Hop answer JSON", prompt)
        self.assertIn("tasks_summary", prompt)
        self.assertIn("gap_closure_summary", prompt)
        self.assertNotIn("apply_scaffold/agent.py", prompt)
        self.assertNotIn("opsx/opsx_runtime/agents", prompt)
        self.assertNotRegex(
            prompt,
            r"(?i)read\s+.*apply_scaffold/agent\.py",
        )
        self.assertIn("SCAFFOLD_READY", prompt)
        self.assertIn("BLOCKED", prompt)
        self.assertIn("SCAFFOLD_COMPLETE", prompt)
        self.assertIn("Hop success after bounded shape work", prompt)
        self.assertIn("apply_design_identity", prompt)
        scope = resolve_read_scope("apply_scaffold")
        repo_root = Path(__file__).resolve().parents[5]
        for blocked in (
            "opsx/opsx_runtime/agents/implementation/apply_scaffold/agent.py",
            "openspec/schemas/opsx.apply_scaffold.json",
        ):
            result = check_paths_in_scope(
                scope,
                tool_name="read",
                paths=[blocked],
                repo_root=repo_root,
            )
            self.assertTrue(result.violations, msg=blocked)
            self.assertEqual(result.violations[0].rule, "outside_restrict_allow")

    def test_red_prompt_escalates_incomplete_design_bindings(self) -> None:
        from agents.implementation.apply_red.prompt.get_prompt import get_prompt as red_prompt

        prompt = red_prompt(
            repository_path="C:/repo",
            change_id="visual",
            impl_verify_answer={"slice_id": "SLICE-009"},
            artifact_bundle={},
            machine_context={},
        )
        self.assertIn("ESCALATE_ARBITER", prompt)
        self.assertIn("arbiter_request", prompt)

    def test_implement_prompt_escalates_design_gap(self) -> None:
        from agents.implementation.apply_implement.prompt.get_prompt import (
            get_prompt as implement_prompt,
        )

        prompt = implement_prompt(
            repository_path="C:/repo",
            change_id="visual",
            impl_verify_answer={"slice_id": "SLICE-001"},
            artifact_bundle={},
            machine_context={},
        )
        self.assertIn("ESCALATE_ARBITER", prompt)
        self.assertIn("arbiter_request", prompt)

    def test_green_prompt_allows_design_redo_block(self) -> None:
        from agents.implementation.apply_green.prompt.get_prompt import get_prompt as green_prompt

        prompt = green_prompt(
            repository_path="C:/repo",
            change_id="visual",
            impl_verify_answer={"slice_id": "SLICE-001"},
            artifact_bundle={},
            machine_context={},
        )
        self.assertIn("Slice closing stage only", prompt)

    def test_apply_design_prompt_uses_embedded_authority(self) -> None:
        prompt = design_prompt(
            repository_path="C:/repo",
            change_id="visual",
            impl_verify_answer={},
            artifact_bundle={},
            machine_context={},
        )
        self.assertIn("APPLY DESIGN STAGE AUTHORITY", prompt)
        self.assertIn("ApplyDesignAgent", prompt)
        self.assertNotIn(".cursor/commands", prompt)
        self.assertNotIn("BEGIN /opsx-apply-design", prompt)

    def test_apply_design_review_prompt_uses_embedded_authority(self) -> None:
        prompt = design_review_prompt(
            repository_path="C:/repo",
            change_id="visual",
            impl_verify_answer={},
            artifact_bundle={},
            machine_context={},
        )
        self.assertIn("APPLY DESIGN REVIEW STAGE AUTHORITY", prompt)
        self.assertIn("ApplyDesignReviewAgent", prompt)
        self.assertNotIn(".cursor/commands", prompt)


if __name__ == "__main__":
    unittest.main()
