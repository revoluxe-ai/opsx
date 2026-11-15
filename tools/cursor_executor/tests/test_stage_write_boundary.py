"""Stage write boundary preflight for Super Implement SDK transport."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from cursor_executor.schemas import CursorExecutionRequest, ModelRoute, RuntimeKind, WritePolicy
from cursor_executor.stage_write_boundary import validate_super_implement_stage_write_boundary


class StageWriteBoundaryTests(unittest.TestCase):
    def _req(self, **meta):
        return CursorExecutionRequest(
            execution_id="e1",
            workspace=".",
            runtime=RuntimeKind.LOCAL,
            model_route=ModelRoute(model_id="m", params={}),
            prompt="/opsx-apply-scaffold change=c slice=S",
            write_policy=WritePolicy.MUTATING,
            metadata={"super_implement": True, **meta},
        )

    def test_missing_globs_blocked(self) -> None:
        block = validate_super_implement_stage_write_boundary(self._req())
        self.assertIsNotNone(block)
        self.assertEqual(block.code, "STAGE_WRITE_BOUNDARY_METADATA_MISSING")

    def test_slash_imitation_mutating_blocked(self) -> None:
        block = validate_super_implement_stage_write_boundary(
            self._req(
                permitted_write_globs=["apply-scaffold/**"],
                slash_prompt_imitation=True,
            )
        )
        self.assertIsNotNone(block)
        self.assertEqual(block.code, "STAGE_WRITE_SDK_SLASH_IMITATION_FORBIDDEN")

    def test_enforcement_required(self) -> None:
        block = validate_super_implement_stage_write_boundary(
            self._req(
                permitted_write_globs=["apply-scaffold/**"],
                stage_write_boundary_enforcement="metadata_only",
            )
        )
        self.assertIsNotNone(block)
        self.assertEqual(block.code, "STAGE_WRITE_BOUNDARY_ENFORCEMENT_REQUIRED")

    def test_isolated_workspace_agent_hop_blocked_without_os_env(self) -> None:
        block = validate_super_implement_stage_write_boundary(
            self._req(
                permitted_write_globs=["openspec/changes/x/apply-red/**", "tests/**"],
                stage_write_boundary_enforcement="isolated_workspace_v0",
                super_implement_agent_hop_v0=True,
                slash_prompt_imitation=False,
            )
        )
        self.assertIsNotNone(block)
        self.assertEqual(block.code, "AGENT_HOP_OS_SANDBOX_ENV_PROOF_MISSING")

    def test_isolated_workspace_agent_hop_allowed_with_os_env(self) -> None:
        with mock.patch.dict(os.environ, {"AGENT_HOP_OS_SANDBOX_ENFORCED": "bwrap"}, clear=False):
            block = validate_super_implement_stage_write_boundary(
                self._req(
                    permitted_write_globs=["openspec/changes/x/apply-red/**", "tests/**"],
                    stage_write_boundary_enforcement="isolated_workspace_v0",
                    super_implement_agent_hop_v0=True,
                    slash_prompt_imitation=False,
                )
            )
        self.assertIsNone(block)


if __name__ == "__main__":
    unittest.main()
