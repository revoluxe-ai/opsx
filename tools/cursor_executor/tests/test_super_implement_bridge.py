"""Super Implement dispatch bridge — command contract + inherit metadata (no SDK)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cursor_executor.super_implement_bridge import (
    build_super_implement_request,
    validate_super_implement_dispatch,
)


REPO = Path(__file__).resolve().parents[3]


class SuperImplementBridgeTests(unittest.TestCase):
    def test_build_request_preserves_inherit_metadata(self) -> None:
        payload = {
            "execution_id": "exec-super-1",
            "cursor_request": {
                "execution_id": "exec-super-1",
                "workspace": str(REPO),
                "runtime": "LOCAL",
                "model_route": {
                    "model_id": "gpt-5.6-sol",
                    "params": {"context": "272k", "reasoning": "high", "fast": "false"},
                },
                "prompt": "/opsx-apply-scaffold change=demo slice=SLICE-001",
                "write_policy": "MUTATING",
                "metadata": {
                    "super_implement": True,
                    "stage": "APPLY_SCAFFOLD",
                    "primitive_command": "/opsx-apply-scaffold",
                },
            },
        }
        req = build_super_implement_request(payload)
        self.assertEqual(req.metadata.get("model_mode"), "INHERIT")
        self.assertEqual(req.metadata.get("requested_model"), "inherit")
        self.assertTrue(req.metadata.get("tier_substitution_forbidden"))
        self.assertEqual(req.write_policy.value, "MUTATING")

    def test_validate_native_scaffold_contract_exists(self) -> None:
        payload = {
            "execution_id": "exec-super-2",
            "cursor_request": {
                "execution_id": "exec-super-2",
                "workspace": str(REPO),
                "runtime": "LOCAL",
                "model_route": {
                    "model_id": "gpt-5.6-sol",
                    "params": {"context": "272k", "reasoning": "high", "fast": "false"},
                },
                "prompt": "/opsx-apply-scaffold change=demo slice=SLICE-001",
                "write_policy": "MUTATING",
                "metadata": {"command_id": "opsx-apply-scaffold"},
            },
        }
        out = validate_super_implement_dispatch(payload)
        self.assertTrue(out["contract_exists"])
        self.assertEqual(out["command_id"], "opsx-apply-scaffold")
        self.assertEqual(out["model_mode"], "INHERIT")
        self.assertTrue(out["ok"])


if __name__ == "__main__":
    unittest.main()
