"""Runtime proof that supervisor never gives an agent generic command execution."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "opsx" / "opsx_runtime"))

from agents.process_repair.supervisor_tool_policy import (
    SUPERVISOR_FIX_TOOLS,
    SupervisorAgentToolPolicyError,
    assert_supervisor_agent_tools,
    assert_supervisor_provider_options,
)


class SupervisorAgentToolPolicyTests(unittest.TestCase):
    def test_existing_registered_tools_are_allowed(self):
        self.assertEqual(
            assert_supervisor_agent_tools(SUPERVISOR_FIX_TOOLS),
            SUPERVISOR_FIX_TOOLS,
        )
        self.assertNotIn("shell", SUPERVISOR_FIX_TOOLS)

    def test_generic_execution_is_forbidden_at_agent_creation(self):
        for name in (
            "shell", "bash", "powershell", "terminal", "exec",
            "run_command", "command", "subprocess", "mcp",
            "apply_patch", "python_exec",
        ):
            with self.subTest(name=name):
                with self.assertRaises(SupervisorAgentToolPolicyError) as caught:
                    assert_supervisor_agent_tools(["read", name])
                self.assertEqual(caught.exception.code, "SUPERVISOR_UNREGISTERED_TOOL_FORBIDDEN")

    def test_duplicate_or_malformed_tool_list_is_rejected(self):
        for requested in ("read", ["read", "read"], ["read", "shell "], [object()]):
            with self.subTest(value=str(requested)):
                with self.assertRaises(SupervisorAgentToolPolicyError):
                    assert_supervisor_agent_tools(requested)

    def test_capability_manifest_cannot_bypass_finite_list(self):
        for manifest in ({}, {"tools": ["shell"]}, {"name": "arbitrary"}):
            with self.subTest(manifest=manifest):
                with self.assertRaises(SupervisorAgentToolPolicyError) as caught:
                    assert_supervisor_agent_tools(
                        ["read"], capability_manifest=manifest,
                    )
                self.assertEqual(caught.exception.code, "SUPERVISOR_CAPABILITY_MANIFEST_FORBIDDEN")

    def test_provider_must_disable_shell(self):
        with self.assertRaises(SupervisorAgentToolPolicyError) as caught:
            assert_supervisor_provider_options(
                {"tools": ["read"], "local": {}, "disallowed_tools": []},
                requested=["read"],
            )
        self.assertEqual(caught.exception.code, "SUPERVISOR_NATIVE_SHELL_NOT_DISABLED")

    def test_provider_shell_injection_rejected(self):
        for name in ("shell", "terminal", "exec", "bash", "run_command"):
            with self.subTest(name=name):
                with self.assertRaises(SupervisorAgentToolPolicyError) as caught:
                    assert_supervisor_provider_options(
                        {"tools": ["read", name], "local": {}, "disallowed_tools": ["shell"]},
                        requested=["read"],
                    )
                self.assertEqual(caught.exception.code, "SUPERVISOR_PROVIDER_TOOL_ESCALATION")

    def test_registered_pytest_is_the_only_mcp_bridge(self):
        assert_supervisor_provider_options(
            {"tools": ["mcp", "read", "edit"], "local": {"custom_tools": {"run_opsx_pytest": object()}}, "disallowed_tools": ["shell"]},
            requested=SUPERVISOR_FIX_TOOLS,
        )
        with self.assertRaises(SupervisorAgentToolPolicyError) as caught:
            assert_supervisor_provider_options(
                {"tools": ["mcp", "read"], "local": {"custom_tools": {"run_opsx_pytest": object()}}, "disallowed_tools": ["shell"]},
                requested=["read"],
            )
        self.assertEqual(caught.exception.code, "SUPERVISOR_UNAUTHORIZED_MCP")

    def test_missing_registered_pytest_bridge_is_rejected(self):
        with self.assertRaises(SupervisorAgentToolPolicyError) as caught:
            assert_supervisor_provider_options(
                {"tools": ["read", "edit"], "local": {}, "disallowed_tools": ["shell"]},
                requested=SUPERVISOR_FIX_TOOLS,
            )
        self.assertEqual(caught.exception.code, "SUPERVISOR_REGISTERED_TOOL_NOT_CONNECTED")

    def test_duplicate_provider_tools_or_invalid_metadata_rejected(self):
        for tools in (["read", "read"], "read", [42]):
            with self.subTest(value=str(tools)):
                with self.assertRaises(SupervisorAgentToolPolicyError):
                    assert_supervisor_provider_options(
                        {"tools": tools, "local": {}, "disallowed_tools": ["shell"]},
                        requested=["read"],
                    )

    def test_arbitrary_custom_tool_forbidden(self):
        with self.assertRaises(SupervisorAgentToolPolicyError) as caught:
            assert_supervisor_provider_options(
                {"tools": ["mcp", "read"], "disallowed_tools": ["shell"],
                 "local": {"custom_tools": {"run_opsx_pytest": object(), "evil_shell": object()}}},
                requested=SUPERVISOR_FIX_TOOLS,
            )
        self.assertEqual(caught.exception.code, "SUPERVISOR_UNREGISTERED_CUSTOM_TOOL")

    def test_external_mcp_server_forbidden(self):
        with self.assertRaises(SupervisorAgentToolPolicyError) as caught:
            assert_supervisor_provider_options(
                {"tools": ["read"], "disallowed_tools": ["shell"],
                 "local": {"mcp_servers": {"command_bridge": {"command": "cmd"}}}},
                requested=["read"],
            )
        self.assertEqual(caught.exception.code, "SUPERVISOR_EXTERNAL_MCP_FORBIDDEN")

    def test_empty_registered_list_is_safe_for_read_only_hop(self):
        assert_supervisor_provider_options(
            {"tools": [], "local": {}, "disallowed_tools": ["shell"]},
            requested=[],
        )


if __name__ == "__main__":
    unittest.main()
